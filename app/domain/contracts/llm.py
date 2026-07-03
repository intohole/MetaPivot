"""ILLMProvider - LLM 提供方抽象接口

支持的实现：
- OpenAICompatProvider：OpenAI 兼容协议（Kimi/Qwen/GLM/DeepSeek 等），当前默认
- 预留扩展：AnthropicProvider / BedrockProvider / 本地 vLLM 等

接口约束：
- 所有方法异步
- messages/tools 参数与 OpenAI Chat Completion API 格式保持一致
- 流式接口返回 AsyncIterator[str]，每个 chunk 是一段增量文本
"""
from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


@runtime_checkable
class ILLMProvider(Protocol):
    """LLM 统一 Provider 接口"""

    async def chat_completion(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> dict[str, Any]:
        """对话补全（支持工具调用）

        Returns:
            {content, tool_calls, finish_reason, usage}
        """
        ...

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """流式对话补全，yield 增量文本"""
        ...

    async def embed(self, text: str, model: str = "text-embedding-v3") -> list[float]:
        """文本向量化（RAG 用）"""
        ...
