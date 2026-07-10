"""replier_node - 最终回复生成节点

从 nodes.py 抽离以控制文件行数（nodes.py 原本 300 行已达上限）。
负责非流式最终回复生成；流式版本由 stream_final_reply 处理（Sprint 7.5 从 graph.py 迁入）。

架构：Domain 层节点，通过函数内延迟 import Service/Infra 层执行 IO，
避免循环依赖（参照 scheduler_node.py 抽离先例）。
"""
from typing import AsyncGenerator

from app.domain.agent.executor import apply_context_trim, record_llm_metrics
from app.domain.agent.context_window import get_context_window_tokens
from app.domain.agent.guardrail import sanitize_output
from app.domain.agent.prompts import REPLY_PROMPT, SYSTEM_PROMPT
from app.domain.agent.state import AgentMode, AgentState, AgentStatus
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("agent_replier")


async def replier_node(state: AgentState) -> dict:
    """回复节点：生成最终回复（非流式版本，流式由 graph 层处理）

    安全加固：LLM 响应必须经 sanitize_output 脱敏后再写入 final_answer，
    防止敏感关键词（jwt_secret/api_key 等）泄露给用户。
    """
    if state.final_answer:
        state.add_event("final_result", {"answer": state.final_answer})
        return {"status": AgentStatus.COMPLETED}

    from app.infra.llm.provider import get_llm
    llm = get_llm()

    if state.mode == AgentMode.PIPELINE or not state.available_tools:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": state.original_message}]
    elif state.messages:
        apply_context_trim(state, get_context_window_tokens(settings.llm_model), "reply")
        state.messages.append({"role": "user", "content": REPLY_PROMPT})
        messages = state.messages
    else:
        return {"status": AgentStatus.COMPLETED, "final_answer": "无法处理您的请求"}

    try:
        result = await llm.chat_completion(messages=messages)
    except Exception as e:
        log.exception("Replier LLM call failed: {}", e)
        state.add_event("error", {"message": str(e)})
        record_llm_metrics({}, 0, "failed")
        return {"status": AgentStatus.FAILED, "error": {"code": "LLM_ERROR", "message": str(e)}}

    # 安全加固：LLM 原始输出必须经 sanitize_output 脱敏
    answer = sanitize_output(result.get("content", ""))
    usage = result.get("usage") or {}
    state.total_tokens += int(usage.get("total_tokens", 0))
    state.add_event("final_result", {"answer": answer, "usage": usage})
    record_llm_metrics(usage, 0)
    return {"status": AgentStatus.COMPLETED, "final_answer": answer,
            "result": {"answer": answer, "usage": usage}, "total_tokens": state.total_tokens}


async def stream_final_reply(state: AgentState) -> AsyncGenerator[dict, None]:
    """流式生成最终回复，yield token 事件（Sprint 7.5 从 graph.py 迁入）

    使用 LLM chat_stream 逐 token 输出，提升用户感知速度。
    失败时静默返回（state.status 不变），由调用方降级为非流式 replier_node。
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
            yield {"type": "token", "data": {"text": token}}
    except Exception as e:
        log.warning("Stream reply failed, fallback to non-stream: {}", e)
        return  # 降级，由调用方走 replier_node

    # 安全加固：流式输出也必须经 sanitize_output 脱敏
    full_answer = sanitize_output(full_answer)
    state.final_answer = full_answer
    state.result = {"answer": full_answer}
    state.status = AgentStatus.COMPLETED
