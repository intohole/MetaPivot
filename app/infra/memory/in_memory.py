"""InMemoryMemoryStore - 进程内多轮对话记忆存储（开发环境）

实现 IMemoryStore Protocol：
- 用 dict 存储 chat_id → list[message]
- 用 dict 存储 chat_id → summary
- 适合开发环境，零外部依赖
- 重启后丢失（生产环境用 DBMemoryStore）

部署场景：MEMORY_BACKEND=memory（小企业开发环境）
"""
from collections import defaultdict
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("memory_in_memory")


class InMemoryMemoryStore:
    """进程内多轮对话记忆存储"""

    def __init__(self) -> None:
        # chat_id → list[dict]（每条形如 {"role", "content", "metadata", "ts"}）
        self._messages: dict[str, list[dict]] = defaultdict(list)
        # chat_id → {"summary": str, "message_count": int}
        self._summaries: dict[str, dict] = {}
        # 每会话最多保留条数（避免内存膨胀）
        self._max_per_chat = 100

    async def load_history(self, chat_id: str, limit: int = 20) -> list[dict]:
        """加载最近 N 条消息（oldest → newest 顺序）"""
        msgs = self._messages.get(chat_id, [])
        # 取最近 limit 条，保持时间顺序
        recent = msgs[-limit:] if len(msgs) > limit else list(msgs)
        # 返回不含内部字段的干净结构
        return [
            {
                "role": m["role"],
                "content": m["content"],
                "metadata": m.get("metadata", {}),
            }
            for m in recent
        ]

    async def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """追加消息到会话历史"""
        import time
        self._messages[chat_id].append({
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "ts": time.time(),
        })
        # 自动截断，保留最近 _max_per_chat 条
        if len(self._messages[chat_id]) > self._max_per_chat:
            self._messages[chat_id] = self._messages[chat_id][-self._max_per_chat:]

    async def get_summary(self, chat_id: str) -> Optional[str]:
        """获取会话摘要"""
        entry = self._summaries.get(chat_id)
        return entry.get("summary") if entry else None

    async def set_summary(
        self, chat_id: str, summary: str, message_count: int = 0
    ) -> None:
        """更新会话摘要"""
        self._summaries[chat_id] = {
            "summary": summary,
            "message_count": message_count,
        }

    async def clear(self, chat_id: str) -> None:
        """清空会话所有消息和摘要"""
        self._messages.pop(chat_id, None)
        self._summaries.pop(chat_id, None)

    async def health(self) -> bool:
        """健康检查（进程内存储始终健康）"""
        return True

    # ============ 语义记忆扩展（进程内无 embedding 能力，默认 no-op）============

    async def append_with_embedding(
        self, chat_id: str, role: str, content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """进程内无 embedding 能力，降级为 append_message（仅存 episodic）"""
        await self.append_message(chat_id, role, content, metadata)

    async def search_semantic(
        self, query: str, chat_id: Optional[str] = None, top_k: int = 5,
    ) -> list[dict]:
        """进程内无语义检索能力，返回空列表（不补充语义记忆）"""
        return []

    async def consolidate_memories(self, chat_id: str) -> None:
        """进程内无事实抽取能力，no-op"""
        return None
