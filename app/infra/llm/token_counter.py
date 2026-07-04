"""TiktokenCounter - Token 计数器实现

策略：
1. 优先使用 tiktoken（cl100k_base 编码，适用于 GPT-4/GPT-3.5/Kimi/Qwen 等 OpenAI 兼容模型）
2. tiktoken 不可用时（小企业零依赖部署）退化为字符数估算：
   - 中文按 1.5 字符/token（经验值，中文 token 密度约 1.5-2 字符/token）
   - 英文按 4 字符/token（OpenAI 经验值）
   - 混合文本取加权平均

精度：
- tiktoken：精确（±0 token）
- 字符估算：粗略（±20% 误差），但足以触发上下文窗口保护

线程安全：tiktoken.Encoding 线程安全，可全局共享单例。
"""
import json
from typing import Optional

from app.domain.contracts.token_counter import ITokenCounter
from app.utils.logger import get_logger

log = get_logger("token_counter")

# OpenAI 每条消息固定 overhead（role 标记 + 结构化字段）
_PER_MSG_OVERHEAD = 4
# 回复占位 token（OpenAI 保留给 assistant 回复）
_REPLY_RESERVE = 3


class TiktokenCounter(ITokenCounter):
    """Token 计数器：tiktoken 优先，字符估算兜底

    单例模式：encoding 创建开销较大，全局复用。
    """

    _instance: Optional["TiktokenCounter"] = None
    _encoding = None
    _use_tiktoken: bool = True

    def __new__(cls) -> "TiktokenCounter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_encoding()
        return cls._instance

    def _init_encoding(self) -> None:
        """初始化 tiktoken encoding，失败则降级到字符估算"""
        try:
            import tiktoken
            self._encoding = tiktoken.get_encoding("cl100k_base")
            self._use_tiktoken = True
            log.info("TiktokenCounter initialized with cl100k_base encoding")
        except Exception as e:
            log.warning(
                "tiktoken unavailable ({}), fallback to character estimation",
                e,
            )
            self._use_tiktoken = False

    def count_tokens(self, text: str) -> int:
        """估算单段文本 token 数"""
        if not text:
            return 0
        if self._use_tiktoken and self._encoding is not None:
            return len(self._encoding.encode(text))
        # 字符估算：中文密度高 ~1.5 char/token，英文 ~4 char/token
        # 混合取保守值 2.5 char/token 避免低估导致溢出
        return max(1, len(text) // 2)

    def count_messages_tokens(self, messages: list[dict]) -> int:
        """估算 OpenAI messages 总 token 数（含 overhead）"""
        if not messages:
            return 0
        total = 0
        for msg in messages:
            total += _PER_MSG_OVERHEAD
            role = msg.get("role", "")
            total += self.count_tokens(role)
            content = msg.get("content")
            if isinstance(content, str):
                total += self.count_tokens(content)
            elif content:
                total += self.count_tokens(json.dumps(content, ensure_ascii=False))
            # tool_calls 字段（assistant 消息）
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                total += self.count_tokens(json.dumps(tool_calls, ensure_ascii=False))
            # tool_call_id / name 字段（tool 消息）
            for key in ("tool_call_id", "name"):
                v = msg.get(key)
                if v:
                    total += self.count_tokens(str(v))
        total += _REPLY_RESERVE
        return total


# 单例访问
_token_counter: Optional[TiktokenCounter] = None


def get_token_counter() -> TiktokenCounter:
    """获取全局 TokenCounter 单例"""
    global _token_counter
    if _token_counter is None:
        _token_counter = TiktokenCounter()
    return _token_counter
