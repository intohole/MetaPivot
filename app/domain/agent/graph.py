"""Agent 状态机 - 自定义状态机推进器

状态流转：
    INTENT → PLANNING → EXECUTING → HITL(可选) → REFLECTING → EXECUTING(循环)/REPLY → COMPLETED

设计：
- 自定义状态机替代 LangGraph，避免 Pydantic + Annotated reducer 兼容性问题
- 通过 DB 持久化 AgentState 实现 checkpoint
- 通过 status=WAITING_CONFIRM + pending_confirm 实现 interrupt
- 通过 confirm_task() 恢复执行
- 通过 async generator 实现 stream 推送
"""
import asyncio
import json
from typing import AsyncGenerator

from app.domain.agent.nodes import (
    executor_node,
    hitl_node,
    intent_node,
    planner_node,
    reflector_node,
    replier_node,
)
from app.domain.agent.state import AgentState, AgentStatus
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
    """
    try:
        # 1. 意图分类
        state = await _advance(intent_node, state)
        yield _event("intent_completed", {"mode": state.mode.value, "intent": state.intent})

        # 2. 规划
        state = await _advance(planner_node, state)
        yield _event("planning_completed", {})

        # 3. 执行循环
        loop_count = 0
        while loop_count < _MAX_LOOP:
            loop_count += 1

            # 检查是否已完成
            if state.status == AgentStatus.COMPLETED:
                break

            if state.status == AgentStatus.REFLECTING:
                state = await _advance(reflector_node, state)
                yield _event("reflected", {"status": state.status.value})
                if state.status == AgentStatus.COMPLETED:
                    break
                if state.status != AgentStatus.EXECUTING:
                    break
                continue

            if state.status == AgentStatus.EXECUTING:
                state = await _advance(executor_node, state)
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
                yield _event("hitl_paused", state.pending_confirm or {})
                return  # 暂停

            # 未预期状态
            log.warning("Unexpected status: {}", state.status)
            break

        # 4. 生成最终回复
        if state.status != AgentStatus.COMPLETED:
            state = await _advance(replier_node, state)

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
    """执行单个节点，应用返回的状态更新"""
    try:
        update = await asyncio.wait_for(node_fn(state), timeout=_STEP_TIMEOUT)
    except asyncio.TimeoutError:
        state.status = AgentStatus.FAILED
        state.error = {"code": "TIMEOUT", "message": f"{node_fn.__name__} 超时"}
        return state

    if isinstance(update, dict):
        for k, v in update.items():
            if hasattr(state, k):
                # list 字段做合并而非覆盖（保留历史）
                current = getattr(state, k)
                if isinstance(current, list) and isinstance(v, list):
                    # 列表合并：如果新值是完整列表（包含旧值），直接替换
                    if v and v[0] in current:
                        setattr(state, k, v)
                    else:
                        setattr(state, k, current + v)
                else:
                    setattr(state, k, v)
    return state


def _event(event_type: str, data: dict) -> dict:
    """构造 SSE 事件"""
    return {"type": event_type, "data": data}
