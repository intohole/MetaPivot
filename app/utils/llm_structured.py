"""LLM Structured Output 公共 helper

Phase 4.1 共享基础设施：抽取 extractor.py 的 JSON 模式逻辑，
供 query_router / external_verifier / approval_ai_stage 等多处复用。

集成熔断器保护（LLMProvider 内置）+ JSON 解析容错。
"""
import json
from typing import Any, Optional

from app.infra.llm.provider import get_llm
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("llm_structured")


async def llm_json_call(
    system_prompt: str,
    user_input: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 800,
    fallback: Optional[dict] = None,
) -> dict[str, Any]:
    """LLM structured output 调用（JSON 模式）

    Args:
        system_prompt: 系统提示词（含 JSON schema 说明）
        user_input: 用户输入
        temperature: 温度（默认 0.3，结构化输出偏低）
        max_tokens: 最大 token
        fallback: LLM 失败/解析失败时的兜底返回（None 则抛 AppError）

    Returns:
        LLM 输出的 JSON dict

    Raises:
        AppError(ErrorCode.LLM_RESPONSE_INVALID): 解析失败且无 fallback
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    llm = get_llm()
    try:
        result = await llm.chat_completion(
            messages,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        log.warning("llm_json_call LLM failed: {}", e)
        if fallback is not None:
            return fallback
        raise AppError(ErrorCode.LLM_RESPONSE_INVALID, f"LLM 调用失败: {e}", 503)

    content = result.get("content", "{}")
    # 熔断降级返回的文案非 JSON，需容错
    if result.get("finish_reason") == "circuit_open":
        log.warning("llm_json_call circuit open, using fallback")
        if fallback is not None:
            return fallback
        raise AppError(ErrorCode.LLM_RESPONSE_INVALID, "LLM 熔断中", 503)

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        log.error("llm_json_call parse failed: {} | content={}", e, content[:200])
        if fallback is not None:
            return fallback
        raise AppError(ErrorCode.LLM_RESPONSE_INVALID, "LLM 输出 JSON 解析失败", 500)
