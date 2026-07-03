"""LLM Provider - OpenAI兼容，支持Kimi/Qwen/GLM/DeepSeek切换

集成熔断器：连续失败自动熔断，避免雪崩。
"""
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("llm")


class LLMProvider:
    """LLM统一Provider，所有调用异步，集成熔断器"""

    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
        )
        self.model = settings.llm_model
        self.max_retries = 3

    async def chat_completion(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> dict[str, Any]:
        """对话补全（支持工具调用，集成熔断器）"""
        # 熔断器检查
        from app.infra.llm.circuit_breaker import get_circuit_breaker
        breaker = get_circuit_breaker()
        allowed, reason = await breaker.allow_request()
        if not allowed:
            log.warning("LLM call blocked by circuit breaker: {}", reason)
            return {
                "content": None,
                "tool_calls": None,
                "finish_reason": "circuit_open",
                "error": reason,
                "usage": None,
            }

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = await self._call_with_retry(kwargs)
            await breaker.record_success()
            return response
        except Exception as e:
            await breaker.record_failure()
            log.error("LLM chat completion failed: {}", e)
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _call_with_retry(self, kwargs: dict) -> dict[str, Any]:
        """带 tenacity 重试的实际 LLM 调用"""
        response = await self.client.chat.completions.create(**kwargs)
        return {
            "content": response.choices[0].message.content,
            "tool_calls": response.choices[0].message.tool_calls,
            "finish_reason": response.choices[0].finish_reason,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            } if response.usage else None,
        }

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """流式对话补全（集成熔断器）"""
        from app.infra.llm.circuit_breaker import get_circuit_breaker
        breaker = get_circuit_breaker()
        allowed, reason = await breaker.allow_request()
        if not allowed:
            log.warning("LLM stream blocked by circuit breaker: {}", reason)
            return  # 空 async generator

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            await breaker.record_success()
        except Exception as e:
            await breaker.record_failure()
            log.error("LLM stream failed: {}", e)
            raise

    async def embed(self, text: str, model: str = "text-embedding-v3") -> list[float]:
        """文本向量化（RAG用）"""
        try:
            response = await self.client.embeddings.create(model=model, input=text)
            return response.data[0].embedding
        except Exception as e:
            log.error("LLM embed failed: {}", e)
            raise


# 单例
_llm_provider: Optional[LLMProvider] = None


def get_llm() -> LLMProvider:
    """获取LLM Provider单例"""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMProvider()
    return _llm_provider
