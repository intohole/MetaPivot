"""Agent 状态机 - 自定义状态机推进器

状态流转：
    INTENT → PLANNING → EXECUTING → HITL(可选) → REFLECTING → EXECUTING(循环)/REPLY → COMPLETED

设计：
- 自定义状态机替代 LangGraph，避免 Pydantic + Annotated reducer 兼容性问题
- 通过 DB 持久化 AgentState 实现 checkpoint
- 通过 status=WAITING_CONFIRM + pending_confirm 实现 interrupt
- 通过 confirm_task() 恢复执行
- 通过 async generator 实现 stream 推送（含 token 级流式回复）
"""
import asyncio
from typing import AsyncGenerator

from app.domain.agent.nodes import (
    executor_node,
    hitl_node,
    intent_node,
    planner_node,
    reflector_node,
    replier_node,
)
from app.domain.agent.scheduler_node import scheduler_node
from app.domain.agent.prompts import REPLY_PROMPT, SYSTEM_PROMPT
from app.domain.agent.state import AgentMode, AgentState, AgentStatus
from app.utils.logger import get_logger

log = get_logger("agent_graph")

# 单步执行超时（秒）
_STEP_TIMEOUT = 120
# 最大循环次数保护
_MAX_LOOP = 15


async def run_agent(state: AgentState) -> AsyncGenerator[dict, None]:
    """运行 Agent 状态机，yield 事件流

    状态机推进顺序：
        intent → planner → (executor → reflector)* → replier → end
    遇到 WAITING_CONFIRM 则暂停（yield 事件后返回，等待 confirm 恢复）

    最终回复优先使用流式输出（token 事件），失败时降级为非流式。
    每个节点执行后会通过 state.add_event() 累积节点级事件（step_started/llm_call/stuck_detected 等），
    这里统一 drain 并 yield 到 SSE，保证链路可见性。
    """
    try:
        # 1. 意图分类（LLM）
        state = await _advance(intent_node, state)
        for ev in _drain_events(state):
            yield ev
        yield _event("intent_completed", {"mode": state.mode.value, "intent": state.intent})

        # 2. 规划
        state = await _advance(planner_node, state)
        for ev in _drain_events(state):
            yield ev
        yield _event("planning_completed", {})

        # 2.1 定时任务模式：路由到 scheduler_node（创建调度任务后直接结束）
        if state.mode == AgentMode.SCHEDULE:
            state = await _advance(scheduler_node, state)
            for ev in _drain_events(state):
                yield ev
            yield _event("final_result", {
                "answer": state.final_answer,
                "result": state.result,
            })
            state.status = AgentStatus.COMPLETED
            return

        # 3. 执行循环（并行工具调用）
        loop_count = 0
        while loop_count < _MAX_LOOP:
            loop_count += 1

            if state.status == AgentStatus.COMPLETED:
                break

            if state.status == AgentStatus.REFLECTING:
                state = await _advance(reflector_node, state)
                for ev in _drain_events(state):
                    yield ev
                yield _event("reflected", {"status": state.status.value})
                if state.status == AgentStatus.COMPLETED:
                    break
                if state.status != AgentStatus.EXECUTING:
                    break
                continue

            if state.status == AgentStatus.EXECUTING:
                state = await _advance(executor_node, state)
                for ev in _drain_events(state):
                    yield ev
                yield _event("step_completed", {
                    "step": state.current_step,
                    "status": state.status.value,
                })
                # HITL 暂停
                if state.status == AgentStatus.WAITING_CONFIRM:
                    yield _event("human_confirm_required", state.pending_confirm or {})
                    return  # 暂停，等待 confirm 恢复
                continue

            if state.status == AgentStatus.WAITING_CONFIRM:
                state = await _advance(hitl_node, state)
                for ev in _drain_events(state):
                    yield ev
                yield _event("hitl_paused", state.pending_confirm or {})
                return  # 暂停

            log.warning("Unexpected status: {}", state.status)
            break

        # 4. 生成最终回复（优先流式）
        # FAILED 状态直接结束，不再调用 LLM（避免 tenacity 重试拖慢 finally 块执行）
        if state.status == AgentStatus.FAILED:
            yield _event("final_result", {
                "answer": state.final_answer or "",
                "result": state.result,
                "error": state.error,
            })
            return  # 不覆盖 FAILED 状态

        if state.status != AgentStatus.COMPLETED:
            # 尝试流式回复，逐 token 推送
            async for token_event in _stream_final_reply(state):
                yield token_event

            # 流式失败（state 仍非 COMPLETED），降级为非流式
            if state.status != AgentStatus.COMPLETED:
                state = await _advance(replier_node, state)
                for ev in _drain_events(state):
                    yield ev

        yield _event("final_result", {
            "answer": state.final_answer,
            "result": state.result,
        })
        state.status = AgentStatus.COMPLETED

    except Exception as e:
        log.exception("Agent run failed: {}", e)
        state.status = AgentStatus.FAILED
        state.error = {"code": "AGENT_ERROR", "message": str(e)}
        yield _event("error", state.error)


async def _stream_final_reply(state: AgentState) -> AsyncGenerator[dict, None]:
    """流式生成最终回复，yield token 事件

    使用 LLM chat_stream 逐 token 输出，提升用户感知速度。
    失败时静默返回（state.status 不变），由调用方降级为非流式。
    """
    from app.infra.llm.provider import get_llm

    llm = get_llm()

    # 构造最终回复的 messages
    if state.mode == AgentMode.PIPELINE or not state.available_tools:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": state.original_message},
        ]
    elif state.messages:
        messages = list(state.messages) + [{"role": "user", "content": REPLY_PROMPT}]
    else:
        return  # 无可用上下文，降级

    full_answer = ""
    try:
        async for token in llm.chat_stream(messages=messages):
            full_answer += token
            yield _event("token", {"text": token})
    except Exception as e:
        log.warning("Stream reply failed, fallback to non-stream: {}", e)
        return  # 降级，由调用方走 replier_node

    state.final_answer = full_answer
    state.result = {"answer": full_answer}
    state.status = AgentStatus.COMPLETED


async def resume_agent(state: AgentState) -> AsyncGenerator[dict, None]:
    """恢复 HITL 暂停的 Agent

    根据 confirm_decision 决定：
    - approve: 标记该步已确认 → 继续 EXECUTING
    - reject: 标记拒绝 → 进入 replier 或 FAILED
    - modify: 应用 modifications → 继续 EXECUTING
    """
    pending = state.pending_confirm
    if pending is None:
        yield _event("error", {"message": "无待确认步骤"})
        return

    decision = state.confirm_decision or "approve"
    step_index = pending.get("step", 0)

    # 更新对应步骤的确认状态
    for step in state.steps:
        if step.step_index == step_index:
            step.confirm_decision = decision
            step.confirm_user = state.user_id
            break

    if decision == "reject":
        state.status = AgentStatus.FAILED
        state.error = {"code": "AGENT_HUMAN_REJECTED", "message": "用户拒绝执行"}
        yield _event("rejected", state.error)
        state.final_answer = "操作已取消"
        return

    # approve / modify：继续执行
    if decision == "modify" and state.confirm_modifications:
        # 应用修改到 pending_confirm.input
        pending["input"] = state.confirm_modifications
        # 同时更新对应 step 的 tool_input
        for step in state.steps:
            if step.step_index == step_index:
                step.tool_input = state.confirm_modifications
                break

    state.pending_confirm = None
    state.confirm_decision = None
    state.confirm_modifications = None
    state.status = AgentStatus.EXECUTING

    # 继续执行循环
    async for event in run_agent(state):
        yield event


async def _advance(node_fn, state: AgentState) -> AgentState:
    """执行单个节点，应用返回的状态更新

    约定：节点返回 dict，其中 list 字段必须返回完整列表（而非增量），
    _advance 直接 setattr 替换。这避免了脆弱的"首元素是否在旧列表"判断。
    """
    try:
        update = await asyncio.wait_for(node_fn(state), timeout=_STEP_TIMEOUT)
    except asyncio.TimeoutError:
        state.status = AgentStatus.FAILED
        state.error = {"code": "TIMEOUT", "message": f"{node_fn.__name__} 超时"}
        return state

    if isinstance(update, dict):
        for k, v in update.items():
            if hasattr(state, k):
                setattr(state, k, v)
    return state


def _event(event_type: str, data: dict) -> dict:
    """构造 SSE 事件"""
    return {"type": event_type, "data": data}


def _drain_events(state: AgentState) -> list[dict]:
    """提取节点累积的节点级事件（step_started/llm_call/stuck_detected 等）

    节点通过 state.add_event() 累积事件，run_agent 在每个节点执行后调用本函数
    将事件 drain 到 SSE，保证链路可见性（节点级事件原本永远不到达订阅者）。
    """
    events = list(state.events)
    state.events.clear()
    return events
