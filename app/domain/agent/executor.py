"""Executor 辅助函数 - 工具调用执行 + 输出截断 + 上下文裁剪

从 nodes.py 抽离 executor 相关的辅助函数，保持 nodes.py 行数在 300 行内。
- truncate_tool_output：智能截断工具输出（保护 JSON 结构）
- execute_tool_call：执行单个工具调用（可并行，无状态副作用）
- apply_context_trim：裁剪超预算消息（executor/replier 共用）
"""
import json
from datetime import datetime
from typing import Any, Optional

from app.domain.agent.state import AgentState, StepRecord
from app.utils.logger import get_logger

log = get_logger("agent_executor")


def truncate_tool_output(output: Any, max_chars: int = 2000) -> str:
    """智能截断工具输出：保留 JSON 结构，超长字段递归截断

    避免简单字符截断破坏 JSON 结构导致 LLM 难以解析。
    """
    text = json.dumps(output, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    # 递归截断长字段
    if isinstance(output, dict):
        truncated: dict = {}
        for k, v in output.items():
            s = json.dumps(v, ensure_ascii=False, default=str)
            truncated[k] = s[:500] + "...[truncated]" if len(s) > 500 else v
        return json.dumps(truncated, ensure_ascii=False, default=str)
    return text[:max_chars] + "...[truncated]"


async def execute_tool_call(state: AgentState, tc: Any) -> StepRecord:
    """执行单个工具调用（可并行，无状态副作用）

    流程：
    0. 内置工具早分支（finish / delegate_to_subagent）— Phase 1
    1. 解析 tool_call arguments（JSON）
    2. 查找 skill_id（不存在则 failed）
    3. 检查 HITL（require_confirm=True 则 waiting_confirm，不执行）
    4. 调用 skill_service.execute（单独计时，便于拆分 LLM vs 工具耗时）
    5. 记录 duration_ms / tool_duration_ms / status / tool_output

    异常由调用方通过 asyncio.gather(return_exceptions=True) 捕获。
    """
    from app.service.skill_service import skill_service
    started = datetime.now()
    tool_name = tc.function.name
    try:
        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
    except json.JSONDecodeError:
        args = {}

    # Phase 1: 内置工具早分支（不经过 skill_service，避免污染业务工具）
    if tool_name == "finish":
        summary = args.get("summary", "")
        return StepRecord(
            step_index=state.current_step, step_name="call_finish",
            tool_name="finish", tool_input=args, status="finish",
            tool_output={"summary": summary}, duration_ms=0,
        )
    if tool_name == "delegate_to_subagent":
        from app.domain.agent.sub_agent import spawn_sub_agent
        message = args.get("message", "")
        max_steps = args.get("max_steps", 5)
        sub_result = await spawn_sub_agent(state, message, max_steps)
        # 子代理 token 用量累计到父任务
        state.total_tokens += int(sub_result.get("tokens", 0))
        return StepRecord(
            step_index=state.current_step, step_name="call_delegate",
            tool_name="delegate_to_subagent", tool_input=args, status="success",
            tool_output=sub_result, duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            token_usage={"total_tokens": sub_result.get("tokens", 0)},
        )

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

    # 实际执行（单独计时，便于拆分 LLM vs 工具耗时）
    tool_started = datetime.now()
    try:
        result = await skill_service.execute(skill_id, args, user_id=state.user_id)
        step.tool_output = result
        step.status = "failed" if "error" in result else "success"
    except Exception as e:
        step.tool_output = {"error": str(e)}
        step.status = "failed"
        step.error = str(e)
    step.tool_duration_ms = int((datetime.now() - tool_started).total_seconds() * 1000)
    step.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    return step


def apply_context_trim(state: AgentState, max_tokens: int, phase: str) -> None:
    """裁剪超预算消息（executor_node / replier_node 共用）

    保留 system + 最近消息 + 完整 tool_call↔tool_result 对。
    超预算时从最旧开始丢弃，并发布 context_trimmed 事件到 SSE。
    失败时静默降级（保留原 messages，由 LLM 端报错或截断）。
    """
    try:
        from app.domain.agent.context_window import trim_messages
        from app.infra.llm.token_counter import get_token_counter
        token_counter = get_token_counter()
        trimmed = trim_messages(state.messages, max_tokens, token_counter)
        if len(trimmed) < len(state.messages):
            state.add_event("context_trimmed", {
                "before": len(state.messages), "after": len(trimmed), "phase": phase,
            })
            state.messages = trimmed
    except Exception as e:
        log.warning("trim_messages (phase={}) failed, fallback: {}", phase, e)


def record_llm_metrics(usage: dict, duration_ms: int, status: str = "success") -> None:
    """记录 LLM 调用指标 + Token 用量（executor_node / replier_node 共用）

    封装 record_llm_call + record_token_usage，避免在每个节点重复导入 metrics 模块。
    duration_ms=0 时不记录延迟（replier_node 未计时场景）。
    """
    from app.utils.config import settings
    from app.utils.metrics import record_llm_call, record_token_usage
    record_llm_call(settings.llm_model, status, duration_ms / 1000 if duration_ms else None)
    if usage:
        record_token_usage(usage)
