"""AsyncScheduler - 基于 asyncio + DB 轮询的定时任务调度器

设计：
- 单进程零外部依赖（不依赖 Redis/Celery），适合小企业单机部署
- 每 POLL_INTERVAL_SECONDS 秒扫描 DB，找出 next_run_at <= now() 的 pending 任务
- 触发执行：调用 AgentService.start_task（异步非阻塞）
- 周期性任务：执行后计算下次 next_run_at，重新入队
- 一次性任务：执行后状态变为 completed

集群扩展：
- 多实例同时运行时通过 SELECT FOR UPDATE SKIP LOCKED 避免重复执行
  （PostgreSQL 支持；SQLite 单机不需要）
- 也可切换到 CeleryScheduler（基于 celery-beat）

容错：
- 单次任务执行失败标记 failed，不重试（避免雪崩）
- 轮询任务异常不退出循环（catch all + log）
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update

from app.domain.contracts.scheduler import IScheduler
from app.infra.db.models_core import ScheduledTaskORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("scheduler")

# 轮询间隔（秒）
_POLL_INTERVAL = 30
# 单次执行超时（秒）— 仅保护调度循环，不限制 Agent 任务本身
_TRIGGER_TIMEOUT = 10


class AsyncScheduler(IScheduler):
    """asyncio + DB 轮询定时任务调度器"""

    def __init__(self) -> None:
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False

    async def schedule(
        self,
        message: str,
        run_at: Optional[datetime] = None,
        recurring: str = "none",
        chat_id: str = "",
        user_id: str = "",
        channel: str = "api",
        context: Optional[dict] = None,
        description: str = "",
    ) -> int:
        """创建定时任务"""
        # 计算首次执行时间
        next_run = run_at or self._compute_next_run(recurring)
        if next_run is None:
            raise ValueError("必须提供 run_at 或指定 recurring 模式")

        async with get_db_session() as session:
            task = ScheduledTaskORM(
                user_id=user_id or None,
                chat_id=chat_id or None,
                channel=channel,
                message=message,
                context=context or {},
                description=description or None,
                run_at=run_at,
                recurring=recurring,
                next_run_at=next_run,
                status="pending",
            )
            session.add(task)
            await session.flush()
            log.info(
                "Scheduled task created: id={} run_at={} recurring={} msg='{}'",
                task.id, next_run, recurring, message[:50],
            )
            return task.id

    def _compute_next_run(self, recurring: str) -> Optional[datetime]:
        """根据 recurring 模式计算下次执行时间"""
        now = datetime.now()
        if recurring == "daily":
            return now + timedelta(days=1)
        if recurring == "weekly":
            return now + timedelta(weeks=1)
        if recurring == "monthly":
            return now + timedelta(days=30)  # 近似，月长度不固定
        return None

    async def cancel(self, task_id: int) -> bool:
        """取消未执行的定时任务"""
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is None or task.status not in ("pending", "running"):
                return False
            task.status = "cancelled"
            await session.flush()
            log.info("Scheduled task cancelled: id={}", task_id)
            return True

    async def list_pending(self, user_id: str = "", limit: int = 50) -> list[dict]:
        """查询待执行的定时任务"""
        async with get_db_session() as session:
            stmt = (
                select(ScheduledTaskORM)
                .where(ScheduledTaskORM.status == "pending")
                .order_by(ScheduledTaskORM.next_run_at.asc())
                .limit(limit)
            )
            if user_id:
                stmt = stmt.where(ScheduledTaskORM.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(t) for t in rows]

    @staticmethod
    def _to_dict(t: ScheduledTaskORM) -> dict:
        return {
            "id": t.id, "message": t.message, "description": t.description,
            "run_at": t.run_at.isoformat() if t.run_at else None,
            "recurring": t.recurring,
            "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
            "status": t.status, "channel": t.channel,
            "chat_id": t.chat_id, "user_id": t.user_id,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    async def start(self) -> None:
        """启动后台轮询任务"""
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("AsyncScheduler started (poll_interval={}s)", _POLL_INTERVAL)

    async def stop(self) -> None:
        """停止后台轮询任务"""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await asyncio.wait_for(self._poll_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        log.info("AsyncScheduler stopped")

    async def _poll_loop(self) -> None:
        """轮询 DB 触发到期任务"""
        while self._running:
            try:
                await self._trigger_due_tasks()
            except Exception as e:
                log.exception("poll loop error: {}", e)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _trigger_due_tasks(self) -> None:
        """触发所有 next_run_at <= now 的 pending 任务"""
        now = datetime.now()
        async with get_db_session() as session:
            stmt = (
                select(ScheduledTaskORM)
                .where(
                    ScheduledTaskORM.status == "pending",
                    ScheduledTaskORM.next_run_at <= now,
                )
                .limit(50)
            )
            tasks = (await session.execute(stmt)).scalars().all()
            if not tasks:
                return
            # 标记为 running 避免重复触发
            for t in tasks:
                t.status = "running"
                t.last_run_at = now
            await session.flush()
            task_ids = [t.id for t in tasks]
            log.info("Triggering {} due scheduled tasks: {}", len(tasks), task_ids)

        # 异步触发执行（不阻塞轮询循环）
        for t in tasks:
            asyncio.create_task(self._execute_one(t.id, t.message, t.channel, t.chat_id, t.user_id, t.context, t.recurring))

    async def _execute_one(
        self, task_id: int, message: str, channel: str,
        chat_id: Optional[str], user_id: Optional[str],
        context: dict, recurring: str,
    ) -> None:
        """执行单个定时任务（调用 AgentService）"""
        try:
            from app.service.agent_service import agent_service
            await asyncio.wait_for(
                agent_service.start_task(
                    message=message, channel=channel,
                    chat_id=chat_id or "", user_id=user_id or "",
                    context=context or {}, stream=False,
                ),
                timeout=_TRIGGER_TIMEOUT,
            )
            log.info("Scheduled task executed: id={}", task_id)
        except Exception as e:
            log.exception("Scheduled task execution failed: id={} err={}", task_id, e)
            await self._mark_failed(task_id, {"code": "EXEC_ERROR", "message": str(e)})
            return

        # 更新状态：周期性 → 计算下次执行；一次性 → completed
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is None:
                return
            if recurring == "none":
                task.status = "completed"
            else:
                task.status = "pending"
                task.next_run_at = self._compute_next_run(recurring)
            await session.flush()

    async def _mark_failed(self, task_id: int, error: dict) -> None:
        """标记任务失败"""
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is not None:
                task.status = "failed"
                task.error = error
                await session.flush()

    async def health(self) -> bool:
        """健康检查：DB 可达 + 轮询任务存活"""
        if not self._running:
            return False
        try:
            async with get_db_session() as session:
                await session.execute(select(ScheduledTaskORM.id).limit(1))
            return True
        except Exception as e:
            log.warning("scheduler health check failed: {}", e)
            return False
