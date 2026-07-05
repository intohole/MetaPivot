"""DBMemoryStore - 数据库持久化多轮对话记忆存储（生产环境）

实现 IMemoryStore Protocol：
- 基于 ChatMessageORM 持久化消息到数据库
- 基于 ChatSummaryORM 存储会话摘要
- 跟随 DB_BACKEND 自动切换 SQLite（单机）/ PostgreSQL（集群）
- 重启不丢失，支持跨实例访问

部署场景：MEMORY_BACKEND=db（生产环境，默认）
依赖：DB_BACKEND=sqlite 或 postgresql
"""
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.infra.db.models_core import ChatMessageORM, ChatSummaryORM
from app.infra.db.session import get_db_session
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("memory_db")


class DBMemoryStore:
    """数据库持久化多轮对话记忆存储"""

    def __init__(self) -> None:
        self._is_sqlite = settings.db_backend == "sqlite"

    async def load_history(self, chat_id: str, limit: int = 20) -> list[dict]:
        """加载最近 N 条消息（oldest → newest 顺序）

        注意：必须用 id 排序而非 created_at——SQLite 的 CURRENT_TIMESTAMP
        精度到秒，同一秒内插入多条消息时 created_at 相同，导致顺序不确定；
        而 id 是 autoincrement 主键，保证按插入顺序单调递增。
        """
        if not chat_id:
            return []
        try:
            async with get_db_session() as session:
                # 用 id 倒序取最近 limit 条，再反转回 oldest → newest
                stmt = (
                    select(ChatMessageORM)
                    .where(ChatMessageORM.chat_id == chat_id)
                    .order_by(ChatMessageORM.id.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                # 反转回 oldest → newest
                rows = list(reversed(rows))
                return [
                    {
                        "role": r.role,
                        "content": r.content,
                        "metadata": r.metadata_ or {},
                    }
                    for r in rows
                ]
        except Exception as e:
            log.warning("load_history failed for {}: {}", chat_id, e)
            return []

    async def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """追加消息到会话历史"""
        if not chat_id:
            return
        try:
            async with get_db_session() as session:
                msg = ChatMessageORM(
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    metadata_=metadata or {},
                )
                session.add(msg)
                await session.flush()
        except Exception as e:
            log.warning("append_message failed for {}: {}", chat_id, e)

    async def get_summary(self, chat_id: str) -> Optional[str]:
        """获取会话摘要"""
        if not chat_id:
            return None
        try:
            async with get_db_session() as session:
                stmt = select(ChatSummaryORM).where(ChatSummaryORM.chat_id == chat_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                return row.summary if row else None
        except Exception as e:
            log.warning("get_summary failed for {}: {}", chat_id, e)
            return None

    async def set_summary(
        self, chat_id: str, summary: str, message_count: int = 0
    ) -> None:
        """更新会话摘要（upsert：存在则更新，不存在则插入）"""
        if not chat_id:
            return
        try:
            async with get_db_session() as session:
                # upsert 兼容 SQLite / PostgreSQL
                if self._is_sqlite:
                    stmt = sqlite_insert(ChatSummaryORM).values(
                        chat_id=chat_id,
                        summary=summary,
                        message_count=message_count,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["chat_id"],
                        set_={
                            "summary": stmt.excluded.summary,
                            "message_count": stmt.excluded.message_count,
                        },
                    )
                else:
                    stmt = pg_insert(ChatSummaryORM).values(
                        chat_id=chat_id,
                        summary=summary,
                        message_count=message_count,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["chat_id"],
                        set_={
                            "summary": stmt.excluded.summary,
                            "message_count": stmt.excluded.message_count,
                        },
                    )
                await session.execute(stmt)
                await session.flush()
        except Exception as e:
            log.warning("set_summary failed for {}: {}", chat_id, e)

    async def clear(self, chat_id: str) -> None:
        """清空会话所有消息和摘要"""
        if not chat_id:
            return
        try:
            async with get_db_session() as session:
                await session.execute(
                    delete(ChatMessageORM).where(ChatMessageORM.chat_id == chat_id)
                )
                await session.execute(
                    delete(ChatSummaryORM).where(ChatSummaryORM.chat_id == chat_id)
                )
                await session.flush()
        except Exception as e:
            log.warning("clear failed for {}: {}", chat_id, e)

    async def health(self) -> bool:
        """健康检查：尝试查询一条消息"""
        try:
            async with get_db_session() as session:
                stmt = select(ChatMessageORM.id).limit(1)
                await session.execute(stmt)
                return True
        except Exception as e:
            log.warning("memory db health check failed: {}", e)
            return False

    # ============ 语义记忆扩展（DB 后端无 embedding 能力，默认降级/空实现）============

    async def append_with_embedding(
        self, chat_id: str, role: str, content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """DB 后端无 embedding 能力，降级为 append_message（仅存 episodic 原文）

        semantic 后端（SemanticMemoryStore）会覆盖此方法，叠加向量入库。
        """
        await self.append_message(chat_id, role, content, metadata)

    async def search_semantic(
        self, query: str, chat_id: Optional[str] = None, top_k: int = 5,
    ) -> list[dict]:
        """DB 后端无语义检索能力，返回空列表（仅 episodic 顺序读，不补充语义记忆）"""
        return []

    async def consolidate_memories(self, chat_id: str) -> None:
        """DB 后端无事实抽取能力，no-op（semantic 后端由 LLM 抽取事实）"""
        return None
