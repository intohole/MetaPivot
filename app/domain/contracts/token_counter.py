"""ITokenCounter - Token 计数器抽象接口

用于 Agent 上下文窗口管理：
- 在每次 LLM 调用前估算 messages 总 token 数
- 超过预算时触发 trim_messages / summarize_messages
- 避免长对话导致上下文溢出（OpenAI 4K/8K/32K/128K 限制）

实现：
- TiktokenCounter：优先 tiktoken cl100k_base（精确，OpenAI 模型适用）
  失败兜底字符数估算（无需 tiktoken 依赖，适合小企业零依赖部署）

接口约束：
- count_tokens 返回非负 int
- count_messages_tokens 接受 OpenAI messages 格式，返回总和
- 线程安全，无状态
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class ITokenCounter(Protocol):
    """Token 计数器统一接口"""

    def count_tokens(self, text: str) -> int:
        """估算单段文本的 token 数

        Args:
            text: 待估算文本

        Returns:
            token 数（非负整数）
        """
        ...

    def count_messages_tokens(self, messages: list[dict]) -> int:
        """估算 OpenAI messages 格式的总 token 数

        每条消息按 role + content + tool_calls 估算，
        加上 OpenAI 每条消息的固定 overhead（约 4 token）。

        Args:
            messages: OpenAI Chat Completion messages 格式

        Returns:
            总 token 数（含 overhead）
        """
        ...
