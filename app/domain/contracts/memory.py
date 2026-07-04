"""IMemoryStore - 多轮对话记忆存储抽象接口

用于 Agent 跨任务多轮记忆，支持单机/集群两种部署：

- InMemoryMemoryStore：进程内 dict 存储，适合开发环境（零依赖，重启丢失）
- DBMemoryStore：基于 ChatMessageORM 持久化到数据库，适合生产环境
  （SQLite 单机 / PostgreSQL 集群，跟随 DB_BACKEND 自动切换）

接口约束：
- load_history() 返回最近 N 条消息（role/content/metadata）， oldest → newest 顺序
- append_message() 追加单条消息（不阻塞主流程）
- get_summary()/set_summary() 长对话压缩摘要（避免无限增长）
- clear() 清空会话（GDPR 合规 / 用户重置）
"""
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class IMemoryStore(Protocol):
    """多轮对话记忆存储统一接口"""

    async def load_history(
        self, chat_id: str, limit: int = 20
    ) -> list[dict]:
        """加载会话历史消息

        Args:
            chat_id: 会话 ID（IM 消息的 chat_id / API 请求的 session_id）
            limit: 最多返回条数（默认 20）

        Returns:
            消息列表，oldest → newest 顺序，每条形如：
            {"role": "user"/"assistant"/"system", "content": "...", "metadata": {...}}
        """
        ...

    async def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """追加单条消息到会话历史

        Args:
            chat_id: 会话 ID
            role: 消息角色（user/assistant/system/tool）
            content: 消息内容
            metadata: 额外元数据（如 tool_call_id, task_id, usage 等）
        """
        ...

    async def get_summary(self, chat_id: str) -> Optional[str]:
        """获取会话的压缩摘要（长对话用）

        Returns:
            摘要文本，无摘要时返回 None
        """
        ...

    async def set_summary(
        self, chat_id: str, summary: str, message_count: int = 0
    ) -> None:
        """更新会话摘要

        Args:
            chat_id: 会话 ID
            summary: 压缩后的摘要文本
            message_count: 生成摘要时的消息数量（用于判断是否需要重新摘要）
        """
        ...

    async def clear(self, chat_id: str) -> None:
        """清空会话所有消息和摘要（GDPR 合规 / 用户重置）"""
        ...

    async def health(self) -> bool:
        """健康检查"""
        ...
