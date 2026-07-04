"""ReplayService - 会话重放数据组装

职责：
1. JOIN agent_tasks + agent_task_events 返回完整事件流
2. 构造 Langfuse UI URL（若启用）
3. 越权防护：仅任务发起人或 admin 可访问

依赖：data/orm（AgentTaskORM + AgentTaskEventORM）
"""
from sqlalchemy import select

from app.infra.db.models_core import AgentTaskEventORM, AgentTaskORM
from app.infra.db.session import get_db_session
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("replay_service")


class ReplayService:
    """会话重放服务"""

    async def get_replay(self, task_id: str, user_id: str = "") -> dict:
        """返回任务详情 + 事件流 + Langfuse URL"""
        async with get_db_session() as session:
            task = await session.get(AgentTaskORM, task_id)
            if task is None:
                raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
            if user_id and task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "无权访问该任务", 403)
            events = await self._list_events(task_id)
            return {
                "task": self._task_dict(task),
                "events": events,
                "langfuse_url": self._langfuse_url(task.trace_id or task.request_id or task.id),
            }

    async def _list_events(self, task_id: str) -> list:
        """查询任务事件流（按 id 排序，即时间顺序）"""
        async with get_db_session() as session:
            stmt = (select(AgentTaskEventORM)
                    .where(AgentTaskEventORM.task_id == task_id)
                    .order_by(AgentTaskEventORM.id))
            rows = (await session.execute(stmt)).scalars().all()
            return [self._event_dict(e) for e in rows]

    def _langfuse_url(self, trace_id: str) -> str:
        """构造 Langfuse UI URL（未启用时返回空串）"""
        if not settings.langfuse_enabled or not settings.langfuse_host:
            return ""
        host = settings.langfuse_host.rstrip("/")
        return f"{host}/trace/{trace_id}"

    def _task_dict(self, t: AgentTaskORM) -> dict:
        return {
            "task_id": t.id, "status": t.status, "channel": t.channel,
            "user_id": t.user_id, "original_message": t.original_message,
            "intent": t.intent, "mode": t.mode,
            "result": t.result, "error": t.error,
            "total_tokens": t.total_tokens,
            "request_id": t.request_id, "trace_id": t.trace_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "duration_ms": t.duration_ms,
        }

    def _event_dict(self, e: AgentTaskEventORM) -> dict:
        return {
            "id": e.id, "event_type": e.event_type, "event_data": e.event_data,
            "step_index": e.step_index, "request_id": e.request_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }


replay_service = ReplayService()
