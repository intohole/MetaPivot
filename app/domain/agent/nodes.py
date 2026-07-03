"""Agent 节点实现 - 自定义状态机各节点函数

节点列表：
- intent_node: LLM 意图分类 → mode (pipeline/agent/workflow)
- planner_node: 构造对话上下文
- executor_node: 并行执行工具调用（asyncio.gather）
- hitl_node: 检查 require_confirm，需要则触发暂停
- reflector_node: 评估是否继续循环（含 stuck 检测）
- replier_node: 生成最终回复（支持流式）

每个节点签名：async def node(state: AgentState) -> dict（返回部分状态更新）

架构说明：
  本模块位于 Domain 层，但 executor_node / _execute_tool_call 通过函数内
  延迟导入 app.service.skill_service 调用 Service 层（运行时回调）。
  这是一种工程妥协：避免循环依赖的同时让节点能执行 IO（工具调用）。
"""
import asyncio
import json
from datetime import datetime
from typing import Any

from app.domain.agent.intent import classify_intent
from app.domain.agent.prompts import REPLY_PROMPT, SYSTEM_PROMPT
from app.domain.agent.state import AgentMode, AgentState, AgentStatus, StepRecord
from app.utils.logger import get_logger
from app.utils.response import ErrorCode

log = get_logger("agent_nodes")


# ============ 意图分类 ============

async def intent_node(state: AgentState) -> dict:
    """意图分类节点：LLM 判断执行模式（替代关键词规则）"""
    state.add_event("step_started", {"step": "intent"})
    from app.infra.llm.provider import get_llm
    from app.service.skill_service import skill_service

    # 预加载可用工具（后续 executor 也会用到）
    tools = await skill_service.list_tools_for_llm(permission="user")
    state.available_tools = tools

    llm = get_llm()
    mode, intent = await classify_intent(state.original_message, tools, llm_provider=llm)

    state.add_event("step_completed", {"step": "intent", "result": {"mode": mode.value, "intent": intent}})
    return {"mode": mode, "intent": intent, "status": AgentStatus.PLANNING, "available_tools": tools}


# ============ 规划 ============

async def planner_node(state: AgentState) -> dict:
    """规划节点：构造对话上下文

    简化实现：直接进入 executor，由 LLM tool_choice=auto 决定调用哪个工具。
    完整版可用 Plan-Execute 模板先生成步骤列表（后续优化项）。
    """
    state.add_event("step_started", {"step": "planning"})
    if state.mode == AgentMode.PIPELINE:
        # 简单问答直接进入回复
        state.add_event("step_completed", {"step": "planning", "result": {"skip_to": "reply"}})
        return {"status": AgentStatus.REFLECTING}

    # AGENT 模式：构造对话上下文
    if not state.messages:
        state.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": state.original_message},
        ]
    state.add_event("step_completed", {"step": "planning"})
    return {"status": AgentStatus.EXECUTING, "messages": state.messages}


# ============ 执行（并行工具调用）============

async def executor_node(state: AgentState) -> dict:
    """执行节点：调用 LLM 并并行执行 tool_calls"""
    from app.infra.llm.provider import get_llm

    state.add_event("step_started", {"step": f"execute_{state.current_step}"})

    if state.current_step >= state.max_steps:
        state.add_event("error", {"code": ErrorCode.AGENT_MAX_STEPS, "message": "达到最大步数"})
        return {"status": AgentStatus.FAILED, "error": {"code": ErrorCode.AGENT_MAX_STEPS}}

    started = datetime.now()
    llm = get_llm()
    tools = state.available_tools

    # Guardrail 输入脱敏
    from app.domain.agent.guardrail import sanitize_messages, sanitize_output
    safe_messages = sanitize_messages(state.messages)

    try:
        result = await llm.chat_completion(
            messages=safe_messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
        )
    except Exception as e:
        log.exception("LLM call failed: {}", e)
        state.add_event("error", {"message": str(e)})
        return {"status": AgentStatus.FAILED, "error": {"code": "LLM_ERROR", "message": str(e)}}

    content = result.get("content")
    if content:
        content = sanitize_output(content)
    tool_calls = result.get("tool_calls") or []
    llm_duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    state.add_event("llm_call", {"duration_ms": llm_duration_ms, "usage": result.get("usage")})

    # 无工具调用 → 进入回复阶段
    if not tool_calls:
        if content:
            return {"status": AgentStatus.COMPLETED, "final_answer": content,
                    "result": {"answer": content, "usage": result.get("usage")}}
        return {"status": AgentStatus.REFLECTING}

    # 记录 assistant 消息（含 tool_calls）
    state.messages.append({"role": "assistant", "content": content, "tool_calls": [
        {"id": tc.id, "type": "function", "function": {
            "name": tc.function.name, "arguments": tc.function.arguments,
        }} for tc in tool_calls
    ]})

    # 并行执行所有工具调用（asyncio.gather）
    tasks = [_execute_tool_call(state, tc) for tc in tool_calls]
    step_records = await asyncio.gather(*tasks, return_exceptions=False)

    # 回填工具结果给 LLM（保持 tool_call_id 关联）
    for step, tc in zip(step_records, tool_calls):
        state.messages.append({
            "role": "tool", "tool_call_id": tc.id,
            "name": tc.function.name,
            "content": json.dumps(step.tool_output, ensure_ascii=False)[:2000],
        })

    # 检查 HITL：若有步骤需要确认且未确认，暂停
    for step in step_records:
        if step.require_confirm and step.confirm_decision is None:
            state.add_event("human_confirm_required", {
                "step": step.step_index, "tool": step.tool_name, "input": step.tool_input,
            })
            return {
                "status": AgentStatus.WAITING_CONFIRM,
                "pending_confirm": {"step": step.step_index, "tool": step.tool_name, "input": step.tool_input},
                "steps": state.steps + list(step_records),
            }

    state.add_event("step_completed", {"step": f"execute_{state.current_step}"})
    return {
        "status": AgentStatus.REFLECTING,
        "messages": state.messages,
        "current_step": state.current_step + 1,
        "steps": state.steps + list(step_records),
    }


async def _execute_tool_call(state: AgentState, tc: Any) -> StepRecord:
    """执行单个工具调用（可并行，无状态副作用）"""
    from app.service.skill_service import skill_service
    started = datetime.now()
    tool_name = tc.function.name
    try:
        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
    except json.JSONDecodeError:
        args = {}

    step = StepRecord(
        step_index=state.current_step, step_name=f"call_{tool_name}",
        tool_name=tool_name, tool_input=args, status="running",
    )

    skill_id = await skill_service.find_skill_id_by_name(tool_name)
    if skill_id is None:
        step.status = "failed"
        step.error = f"Skill '{tool_name}' 不存在或未启用"
        step.tool_output = {"error": step.error}
        step.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        return step

    skill = await skill_service.get_skill(skill_id)
    step.require_confirm = skill.require_confirm if skill else False

    # HITL 检查：需要确认则不执行，等待用户确认
    if step.require_confirm:
        step.status = "waiting_confirm"
        step.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        return step

    # 实际执行
    try:
        result = await skill_service.execute(skill_id, args, user_id=state.user_id)
        step.tool_output = result
        step.status = "failed" if "error" in result else "success"
    except Exception as e:
        step.tool_output = {"error": str(e)}
        step.status = "failed"
        step.error = str(e)
    step.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    return step


# ============ HITL / 反思 / 回复 ============

async def hitl_node(state: AgentState) -> dict:
    """HITL 节点：检查 pending_confirm，需要则等待用户确认"""
    if state.pending_confirm is None:
        return {"status": AgentStatus.REFLECTING}
    state.add_event("human_confirm_required", {
        "step": state.pending_confirm.get("step"),
        "tool": state.pending_confirm.get("tool"),
    })
    return {"status": AgentStatus.WAITING_CONFIRM}


async def reflector_node(state: AgentState) -> dict:
    """反思节点：评估是否继续循环（含 stuck 检测）"""
    if state.status == AgentStatus.COMPLETED:
        return {}
    if state.status == AgentStatus.WAITING_CONFIRM:
        return {}

    # stuck 检测：连续 3 次调用同一工具且失败 → 放弃
    if _is_stuck(state):
        log.warning("Agent stuck detected, giving up")
        state.add_event("stuck_detected", {"step": state.current_step})
        return {"status": AgentStatus.FAILED, "error": {"code": "AGENT_STUCK", "message": "连续工具调用失败，放弃执行"}}

    # 最后消息是 tool 结果 → 继续执行（让 LLM 综合结果）
    if state.messages and state.messages[-1].get("role") == "tool":
        return {"status": AgentStatus.EXECUTING, "current_step": state.current_step}

    return {"status": AgentStatus.COMPLETED if state.final_answer else AgentStatus.REFLECTING}


def _is_stuck(state: AgentState) -> bool:
    """检测是否卡住：连续 3 次失败调用同一工具"""
    recent = state.steps[-3:] if len(state.steps) >= 3 else []
    if len(recent) < 3:
        return False
    tool_names = [s.tool_name for s in recent]
    statuses = [s.status for s in recent]
    # 同一工具且全部失败
    return len(set(tool_names)) == 1 and all(s == "failed" for s in statuses)


async def replier_node(state: AgentState) -> dict:
    """回复节点：生成最终回复（非流式版本，流式由 graph 层处理）"""
    if state.final_answer:
        state.add_event("final_result", {"answer": state.final_answer})
        return {"status": AgentStatus.COMPLETED}

    from app.infra.llm.provider import get_llm
    llm = get_llm()

    if state.mode == AgentMode.PIPELINE or not state.available_tools:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": state.original_message},
        ]
        result = await llm.chat_completion(messages=messages)
        answer = result.get("content", "")
        state.add_event("final_result", {"answer": answer})
        return {"status": AgentStatus.COMPLETED, "final_answer": answer,
                "result": {"answer": answer, "usage": result.get("usage")}}

    # agent 模式：综合工具结果生成最终回复
    if state.messages:
        state.messages.append({"role": "user", "content": REPLY_PROMPT})
        result = await llm.chat_completion(messages=state.messages)
        answer = result.get("content", "")
        state.add_event("final_result", {"answer": answer})
        return {"status": AgentStatus.COMPLETED, "final_answer": answer,
                "result": {"answer": answer, "usage": result.get("usage")}}

    return {"status": AgentStatus.COMPLETED, "final_answer": "无法处理您的请求"}
