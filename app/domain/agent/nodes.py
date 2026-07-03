"""Agent 节点实现 - 自定义状态机各节点函数

节点列表：
- intent_node: 意图分类 → mode (pipeline/agent/workflow/fallback)
- planner_node: 构造对话上下文
- executor_node: 执行工具调用（LLM tool_calls 循环）
- hitl_node: 检查 require_confirm，需要则触发暂停
- reflector_node: 评估是否继续循环
- replier_node: 生成最终回复

每个节点签名：async def node(state: AgentState) -> dict（返回部分状态更新）

架构说明：
  本模块位于 Domain 层，但 executor_node / _execute_tool_call 通过函数内
  延迟导入 app.service.skill_service 调用 Service 层（运行时回调）。
  这是一种工程妥协：避免循环依赖的同时让节点能执行 IO（工具调用）。
  严格 Domain 纯净化改造方向：定义 ToolRuntime Protocol，由 AgentService
  注入实现，节点通过 state.runtime 调用。当前妥协可接受，列为后续优化项。
"""
import json
from datetime import datetime
from typing import Any

from app.domain.agent.state import AgentMode, AgentState, AgentStatus, StepRecord
from app.utils.logger import get_logger
from app.utils.response import ErrorCode

log = get_logger("agent_nodes")

# 系统提示词
_SYSTEM_PROMPT = """你是企业内部办公助手 MetaPivot，帮助员工高效完成工作。
你可以调用已注册的 Skill（包括知识库检索、内部系统 API、MCP 工具等）来解决问题。
规则：
1. 优先使用 Skill 工具获取准确信息，避免凭记忆回答
2. 敏感操作（如审批、删除）会要求用户确认
3. 无法处理时坦诚告知，不要编造信息
4. 回答简洁清晰，使用中文"""

_PLAN_PROMPT = """分析用户请求，制定执行计划。
可用工具：{tools}
用户请求：{message}
输出 JSON：{{"mode": "pipeline|agent|workflow", "steps": [{{"tool": "工具名", "reason": "原因"}}], "intent": "意图描述"}}"""


async def intent_node(state: AgentState) -> dict:
    """意图分类节点：判断执行模式"""
    state.add_event("step_started", {"step": "intent"})
    # 简单规则：有可用工具且消息包含动词性词汇 → agent 模式
    msg = state.original_message
    has_tool_keywords = any(kw in msg for kw in ["查询", "获取", "创建", "申请", "审批", "调用", "执行"])
    if state.available_tools and has_tool_keywords:
        mode = AgentMode.AGENT
        intent = "tool_call"
    elif msg.endswith("?") or msg.endswith("？") or any(kw in msg for kw in ["是什么", "怎么", "如何", "为什么"]):
        mode = AgentMode.PIPELINE
        intent = "qa"
    else:
        mode = AgentMode.AGENT
        intent = "task"

    state.add_event("step_completed", {"step": "intent", "result": {"mode": mode.value, "intent": intent}})
    return {"mode": mode, "intent": intent, "status": AgentStatus.PLANNING}


async def planner_node(state: AgentState) -> dict:
    """规划节点：让 LLM 决定工具调用顺序（ReAct 风格）

    简化实现：直接进入 executor，由 LLM tool_choice=auto 决定调用哪个工具。
    完整版可用 Plan-Execute 模板先生成步骤列表。
    """
    state.add_event("step_started", {"step": "planning"})
    if state.mode == AgentMode.PIPELINE:
        # 简单问答直接进入回复
        state.add_event("step_completed", {"step": "planning", "result": {"skip_to": "reply"}})
        return {"status": AgentStatus.REFLECTING}

    # AGENT 模式：构造对话上下文
    if not state.messages:
        state.messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": state.original_message},
        ]
    state.add_event("step_completed", {"step": "planning"})
    return {"status": AgentStatus.EXECUTING, "messages": state.messages}


async def executor_node(state: AgentState) -> dict:
    """执行节点：调用 LLM 并执行 tool_calls"""
    from app.infra.llm.provider import get_llm
    from app.service.skill_service import skill_service

    state.add_event("step_started", {"step": f"execute_{state.current_step}"})

    if state.current_step >= state.max_steps:
        state.add_event("error", {"code": ErrorCode.AGENT_MAX_STEPS, "message": "达到最大步数"})
        return {"status": AgentStatus.FAILED, "error": {"code": ErrorCode.AGENT_MAX_STEPS}}

    started = datetime.now()
    llm = get_llm()
    tools = await skill_service.list_tools_for_llm(permission="user")

    # Guardrail 输入脱敏：避免 PII 泄露给 LLM
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
    # Guardrail 输出脱敏：移除 LLM 响应中可能泄露的敏感关键词
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

    # 执行工具调用
    state.messages.append({"role": "assistant", "content": content, "tool_calls": [
        {"id": tc.id, "type": "function", "function": {
            "name": tc.function.name, "arguments": tc.function.arguments,
        }} for tc in tool_calls
    ]})

    step_records = []
    for tc in tool_calls:
        step = await _execute_tool_call(state, tc)
        step_records.append(step)
        # 工具结果回填给 LLM
        state.messages.append({
            "role": "tool", "tool_call_id": tc.id,
            "name": tc.function.name, "content": json.dumps(step.tool_output, ensure_ascii=False)[:2000],
        })
        # 检查 HITL
        if step.require_confirm and step.confirm_decision is None:
            state.add_event("human_confirm_required", {
                "step": step.step_index, "tool": step.tool_name,
                "input": step.tool_input,
            })
            return {
                "status": AgentStatus.WAITING_CONFIRM,
                "pending_confirm": {"step": step.step_index, "tool": step.tool_name, "input": step.tool_input},
                "steps": state.steps + step_records,
            }

    state.add_event("step_completed", {"step": f"execute_{state.current_step}"})
    return {
        "status": AgentStatus.REFLECTING,
        "messages": state.messages,
        "current_step": state.current_step + 1,
        "steps": state.steps + step_records,
    }


async def _execute_tool_call(state: AgentState, tc: Any) -> StepRecord:
    """执行单个工具调用"""
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

    # 查询是否需要确认
    skill = await skill_service.get_skill(skill_id)
    step.require_confirm = skill.require_confirm if skill else False

    # HITL 检查
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


async def hitl_node(state: AgentState) -> dict:
    """HITL 节点：检查 pending_confirm，需要则等待用户确认

    在 LangGraph 中通过 interrupt 实现：暂停执行，等待外部 resume。
    """
    if state.pending_confirm is None:
        return {"status": AgentStatus.REFLECTING}

    # 等待用户确认（由 AgentService 处理 interrupt）
    state.add_event("human_confirm_required", {
        "step": state.pending_confirm.get("step"),
        "tool": state.pending_confirm.get("tool"),
    })
    return {"status": AgentStatus.WAITING_CONFIRM}


async def reflector_node(state: AgentState) -> dict:
    """反思节点：评估是否需要继续循环

    简化规则：
    - 如果最后一条消息是 tool 结果 → 继续执行（让 LLM 综合结果）
    - 否则进入回复
    """
    if state.status == AgentStatus.COMPLETED:
        return {}

    if state.status == AgentStatus.WAITING_CONFIRM:
        return {}

    # 检查最后消息
    if state.messages and state.messages[-1].get("role") == "tool":
        return {"status": AgentStatus.EXECUTING, "current_step": state.current_step}

    return {"status": AgentStatus.COMPLETED if state.final_answer else AgentStatus.REFLECTING}


async def replier_node(state: AgentState) -> dict:
    """回复节点：生成最终回复

    - pipeline 模式：直接 LLM 对话回复
    - agent 模式：综合工具调用结果生成回复
    """
    if state.final_answer:
        state.add_event("final_result", {"answer": state.final_answer})
        return {"status": AgentStatus.COMPLETED}

    if state.mode == AgentMode.PIPELINE or not state.available_tools:
        from app.infra.llm.provider import get_llm
        llm = get_llm()
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": state.original_message},
        ]
        result = await llm.chat_completion(messages=messages)
        answer = result.get("content", "")
        state.add_event("final_result", {"answer": answer})
        return {
            "status": AgentStatus.COMPLETED, "final_answer": answer,
            "result": {"answer": answer, "usage": result.get("usage")},
        }

    # agent 模式：综合工具结果生成最终回复
    if state.messages:
        from app.infra.llm.provider import get_llm
        llm = get_llm()
        state.messages.append({
            "role": "user", "content": "请根据以上工具调用结果，给出最终回复。",
        })
        result = await llm.chat_completion(messages=state.messages)
        answer = result.get("content", "")
        state.add_event("final_result", {"answer": answer})
        return {
            "status": AgentStatus.COMPLETED, "final_answer": answer,
            "result": {"answer": answer, "usage": result.get("usage")},
        }

    return {"status": AgentStatus.COMPLETED, "final_answer": "无法处理您的请求"}
