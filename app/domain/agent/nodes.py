"""Agent 节点实现 - 自定义状态机各节点函数

节点：intent_node → planner_node → executor_node → hitl_node → reflector_node
（replier_node 已抽离到 replier.py，控制文件行数）
每个节点签名：async def node(state: AgentState) -> dict（返回部分状态更新）

架构：Domain 层节点通过函数内延迟 import Service 层执行 IO，避免循环依赖。
executor 辅助函数（execute_tool_call / truncate_tool_output / apply_context_trim）
抽离到 executor.py，planner/reflector 逻辑抽离到 planner.py / reflector.py，
scheduler_node 抽离到 scheduler_node.py（仅 SCHEDULE 模式触发）。
"""
import asyncio
from datetime import datetime

from app.domain.agent.builtin_tools import get_builtin_tools
from app.domain.agent.executor import (
    apply_context_trim,
    execute_tool_call,
    record_llm_metrics,
    truncate_tool_output,
)
from app.domain.agent.intent import classify_intent
from app.domain.agent.prompts import SYSTEM_PROMPT, build_system_prompt
from app.domain.agent.state import AgentMode, AgentState, AgentStatus, StepRecord
from app.domain.agent.termination import should_terminate
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.response import ErrorCode

log = get_logger("agent_nodes")


# ============ 意图分类 ============

async def intent_node(state: AgentState) -> dict:
    """意图分类节点：LLM 判断执行模式（替代关键词规则）"""
    state.add_event("step_started", {"step": "intent"})
    from app.infra.llm.provider import get_llm
    from app.service.skill_service import skill_service

    # 预加载可用工具（后续 executor 也会用到）+ 内置工具（finish/delegate）
    tools = await skill_service.list_tools_for_llm(permission="user", tenant_id=state.tenant_id)
    tools = tools + get_builtin_tools()  # Phase 1: 注入内置工具（finish/delegate）
    from app.domain.agent.workflow_tool import get_workflow_tools
    tools = tools + get_workflow_tools()  # Phase 2: 注入 workflow 工具（trigger_workflow/list_workflows）
    # Phase B2: Tool RAG - 工具数 > 15 时按 query embedding 检索 top-10 相关工具子集
    from app.domain.agent.tool_index import get_tool_index
    _ti = get_tool_index()
    if _ti.should_use_rag(len(tools)):
        tools = await _ti.retrieve(state.original_message, tools)
    state.available_tools = tools

    llm = get_llm()
    mode, intent, schedule_result = await classify_intent(
        state.original_message, tools, llm_provider=llm,
    )

    # 检测到定时任务 → 暂存到 context，planner_node 会路由到 scheduler_node
    if schedule_result is not None:
        state.context["schedule_result"] = schedule_result.to_dict()

    state.add_event("step_completed", {"step": "intent", "result": {"mode": mode.value, "intent": intent}})
    return {
        "mode": mode, "intent": intent,
        "status": AgentStatus.PLANNING,
        "available_tools": tools,
        "context": state.context,
    }


# ============ 规划 ============

async def planner_node(state: AgentState) -> dict:
    """规划节点：按 mode 路由

    - SCHEDULE 模式：跳过 plan，直接进入 scheduler_node（COMPLETED）
    - PIPELINE 模式：跳过规划，直接进入回复
    - AGENT 模式：调用 LLM 生成多步计划，注入 system prompt 作为上下文
    - 计划失败不阻塞（plan 为空时 executor 仍可 tool_choice="auto"）
    """
    state.add_event("step_started", {"step": "planning"})
    if state.mode == AgentMode.SCHEDULE:
        # 定时任务模式：路由到 scheduler_node
        state.add_event("step_completed", {"step": "planning", "result": {"route": "scheduler"}})
        return {"status": AgentStatus.COMPLETED}  # scheduler_node 在 graph 层单独处理
    if state.mode == AgentMode.PIPELINE:
        # 简单问答直接进入回复
        state.add_event("step_completed", {"step": "planning", "result": {"skip_to": "reply"}})
        return {"status": AgentStatus.REFLECTING}

    # AGENT 模式：生成执行计划（Plan-Execute）
    from app.infra.llm.provider import get_llm
    from app.domain.agent.planner import generate_plan, format_plan_as_context

    plan: list[dict] = []
    try:
        llm = get_llm()
        plan = await generate_plan(state, llm)
    except Exception as e:
        log.warning("generate_plan failed, fallback to no plan: {}", e)

    # 构造对话上下文：system + plan_context + user
    plan_ctx = format_plan_as_context(plan)
    system_content = SYSTEM_PROMPT + plan_ctx
    if not state.messages:
        state.messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": state.original_message},
        ]
    state.add_event("step_completed", {"step": "planning", "result": {"plan_steps": len(plan)}})
    return {"status": AgentStatus.EXECUTING, "messages": state.messages, "plan": plan}


# ============ 执行（并行工具调用）============

async def executor_node(state: AgentState) -> dict:
    """执行节点：调用 LLM 并并行执行 tool_calls（含 Token 用量追踪 + 上下文裁剪）"""
    from app.infra.llm.provider import get_llm

    state.add_event("step_started", {"step": f"execute_{state.current_step}"})

    if state.current_step >= state.max_steps:
        state.add_event("error", {"code": ErrorCode.AGENT_MAX_STEPS, "message": "达到最大步数"})
        return {"status": AgentStatus.FAILED, "error": {"code": ErrorCode.AGENT_MAX_STEPS}}

    started = datetime.now()
    llm = get_llm()
    tools = state.available_tools

    from app.domain.agent.context_window import get_context_window_tokens
    apply_context_trim(state, get_context_window_tokens(settings.llm_model), "execute")

    # Phase 1: 刷新 system prompt（L2 资源预算可见，促使 LLM 接近上限时主动 finish）
    if state.messages and state.messages[0].get("role") == "system":
        state.messages[0]["content"] = build_system_prompt(state)

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
        record_llm_metrics({}, 0, "failed")
        return {"status": AgentStatus.FAILED, "error": {"code": "LLM_ERROR", "message": str(e)}}

    content = result.get("content")
    if content:
        content = sanitize_output(content)
    tool_calls = result.get("tool_calls") or []
    llm_duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    # Token 用量追踪：累计到 state.total_tokens，落 AgentTaskORM.total_tokens
    usage = result.get("usage") or {}
    state.total_tokens += int(usage.get("total_tokens", 0))
    state.add_event("llm_call", {"duration_ms": llm_duration_ms, "usage": usage})
    record_llm_metrics(usage, llm_duration_ms)

    # 无工具调用 → 进入回复阶段
    if not tool_calls:
        if content:
            return {"status": AgentStatus.COMPLETED, "final_answer": content,
                    "result": {"answer": content, "usage": usage},
                    "total_tokens": state.total_tokens}
        return {"status": AgentStatus.REFLECTING, "total_tokens": state.total_tokens}

    state.messages.append({"role": "assistant", "content": content, "tool_calls": [
        {"id": tc.id, "type": "function", "function": {"name": tc.function.name,
         "arguments": tc.function.arguments}} for tc in tool_calls]})

    # 并行执行所有工具调用（Phase 1: 经 healer 自愈，return_exceptions=True 防止单个异常拖垮整体）
    # Phase C1: 发射 tool_call SSE 事件（前端实时显示工具调用过程：started/completed/failed）
    from app.domain.agent.healer import get_healer
    healer = get_healer()
    for tc in tool_calls:
        state.add_event("tool_call", {"tool": tc.function.name, "args": tc.function.arguments, "status": "started"})
    tasks = [healer.execute_with_healing(state, tc, execute_tool_call) for tc in tool_calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    step_records: list[StepRecord] = []
    for tc, r in zip(tool_calls, results):
        if isinstance(r, Exception):
            state.add_event("tool_call", {"tool": tc.function.name, "status": "failed", "error": str(r)})
            step_records.append(StepRecord(
                step_index=state.current_step, step_name="call_failed", tool_name=tc.function.name,
                status="failed", error=str(r), tool_output={"error": str(r)}, duration_ms=0))
        else:
            state.add_event("tool_call", {"tool": tc.function.name, "status": "completed", "result": getattr(r, "tool_output", None)})
            step_records.append(r)

    # 附加 LLM 调用元数据到步骤（token 用量 + LLM 耗时，便于成本/性能分析）
    for step in step_records:
        step.token_usage = usage or None
        step.llm_duration_ms = llm_duration_ms

    # Phase 1: finish 工具早返回（L1 终止条件 — LLM 显式标记完成）
    finish_step = next((s for s in step_records if s.tool_name == "finish"), None)
    if finish_step:
        summary = (finish_step.tool_output or {}).get("summary", "") or state.final_answer
        state.add_event("step_completed", {"step": f"execute_{state.current_step}", "result": "finish"})
        return {
            "status": AgentStatus.COMPLETED,
            "final_answer": summary,
            "result": {"answer": summary, "usage": usage},
            "messages": state.messages + [{"role": "assistant", "content": content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name,
                 "arguments": tc.function.arguments}} for tc in tool_calls
            ]}],
            "steps": state.steps + list(step_records),
            "total_tokens": state.total_tokens,
        }

    # 回填工具结果给 LLM（保持 tool_call_id 关联，智能截断保护 JSON 结构）
    for step, tc in zip(step_records, tool_calls):
        state.messages.append({
            "role": "tool", "tool_call_id": tc.id,
            "name": tc.function.name,
            "content": truncate_tool_output(step.tool_output),
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
                "total_tokens": state.total_tokens,
            }

    state.add_event("step_completed", {"step": f"execute_{state.current_step}"})
    return {
        "status": AgentStatus.REFLECTING,
        "messages": state.messages,
        "current_step": state.current_step + 1,
        "steps": state.steps + list(step_records),
        "total_tokens": state.total_tokens,
    }


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
    """反思节点：评估是否继续循环（targeted reflection）

    1. stuck 检测（连续 3 次失败同工具）→ FAILED
    2. 触发条件满足时调用 LLM 反思（失败/接近上限/疑似循环）
    3. 快速路径：tool 结果存在 → 继续 EXECUTING；否则 COMPLETED
    """
    if state.status == AgentStatus.COMPLETED:
        return {}
    if state.status == AgentStatus.WAITING_CONFIRM:
        return {}

    # Phase 1: L3 行为终止检测（doom loop / stuck on failure）→ FAILED
    should_stop, reason = should_terminate(state)
    if should_stop:
        log.warning("Agent termination detected: {}", reason)
        state.add_event("stuck_detected", {"step": state.current_step, "reason": reason})
        return {"status": AgentStatus.FAILED, "error": {"code": "AGENT_STUCK", "message": f"终止: {reason}"}}

    # 触发 LLM 反思判断（避免每步都调用，控制成本）
    from app.domain.agent.reflector import should_reflect, reflect, apply_reflect_decision
    if should_reflect(state):
        try:
            from app.infra.llm.provider import get_llm
            llm = get_llm()
            decision, reason, hint = await reflect(state, llm)  # Phase B3: 三元组含 hint
            apply_reflect_decision(state, decision, reason, hint)
            return {"status": state.status, "error": state.error, "context": state.context}
        except Exception as e:
            log.warning("LLM reflect crashed, fallback to default path: {}", e)

    # 快速路径：最后消息是 tool 结果 → 继续执行（让 LLM 综合结果）
    if state.messages and state.messages[-1].get("role") == "tool":
        return {"status": AgentStatus.EXECUTING, "current_step": state.current_step}

    return {"status": AgentStatus.COMPLETED if state.final_answer else AgentStatus.REFLECTING}
