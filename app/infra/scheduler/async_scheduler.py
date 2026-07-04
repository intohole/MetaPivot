"""AsyncScheduler - 基于 asyncio + DB 轮询的定时任务调度器

设计：
- 单进程零外部依赖（不依赖 Redis/Celery），适合小企业单机部署
- 每 _POLL_INTERVAL 秒扫描 DB，找出 next_run_at <= now() 的 pending 任务
- 触发执行：调用 AgentService.start_task（异步非阻塞）
- 周期性任务：执行后计算下次 next_run_at（cron_expr 优先用 croniter，否则用 timedelta）
- 一次性任务：执行后状态变为 completed

Phase 5 增强：
- DLQ + 指数退避：失败时 retry_count += 1，< max_retries 退避重试，>= max_retries 进 DLQ
- PostgreSQL SELECT FOR UPDATE SKIP LOCKED 多实例防重（SQLite 单机无需）
- cron_expr 支持标准 5 段 cron（croniter 解析，比 timedelta 精确）

容错：
- 单次任务执行失败调用 _handle_failure（重试或入 DLQ）
- 轮询任务异常不退出循环（catch all + log）
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from app.domain.contracts.scheduler import IScheduler
from app.domain.agent.cron_helper import next_run_at as _cron_next
from app.infra.db.models_core import ScheduledTaskORM
from app.infra.db.session import get_db_session
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("scheduler")

# 轮询间隔（秒）
_POLL_INTERVAL = 30
# 单次执行超时（秒）— 仅保护调度循环，不限制 Agent 任务本身
_TRIGGER_TIMEOUT = 10
# 默认最大重试次数（DB 字段缺省时的兜底）
_DEFAULT_MAX_RETRIES = 3


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
        cron_expr: str = "",
        chat_id: str = "",
        user_id: str = "",
        channel: str = "api",
        context: Optional[dict] = None,
        description: str = "",
    ) -> int:
        """创建定时任务

        优先级：cron_expr > run_at > recurring
        - cron_expr 非空：用 croniter 计算首次 next_run_at
        - run_at 非空：一次性任务，next_run_at = run_at
        - recurring != "none"：用 _compute_next_run 计算周期
        """
        # 计算首次执行时间
        if cron_expr:
            next_run = _cron_next(cron_expr)
            if next_run is None:
                raise ValueError(f"invalid cron_expr: {cron_expr}")
        elif run_at:
            next_run = run_at
        else:
            next_run = self._compute_next_run(recurring)
        if next_run is None:
            raise ValueError("必须提供 run_at / recurring / cron_expr 之一")

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
                cron_expr=cron_expr or None,
                next_run_at=next_run,
                status="pending",
                retry_count=0,
                max_retries=_DEFAULT_MAX_RETRIES,
            )
            session.add(task)
            await session.flush()
            log.info(
                "Scheduled task created: id={} cron='{}' run_at={} recurring={} msg='{}'",
                task.id, cron_expr or "-", next_run, recurring, message[:50],
            )
            return task.id

    def _compute_next_run(self, recurring: str) -> Optional[datetime]:
        """根据 recurring 模式计算下次执行时间（cron_expr 为空时使用）"""
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

    async def list_dlq(
        self, user_id: str = "", page: int = 1, page_size: int = 20,
    ) -> dict:
        """查询死信队列（retry_count >= max_retries 的 failed 任务）"""
        async with get_db_session() as session:
            base = (
                select(ScheduledTaskORM)
                .where(
                    ScheduledTaskORM.status == "failed",
                    ScheduledTaskORM.retry_count >= ScheduledTaskORM.max_retries,
                )
            )
            if user_id:
                base = base.where(ScheduledTaskORM.user_id == user_id)
            total = (await session.execute(
                select(func.count()).select_from(base.subquery())
            )).scalar() or 0
            stmt = (
                base.order_by(ScheduledTaskORM.updated_at.desc())
                .offset((page - 1) * page_size).limit(page_size)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return {
                "items": [self._to_dict(t) for t in rows],
                "total": total, "page": page, "page_size": page_size,
            }

    async def retry_failed(self, task_id: int, user_id: str = "") -> bool:
        """手动重试失败任务（重置 retry_count=0，状态回 pending，立即入队）"""
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is None or task.status != "failed":
                return False
            if user_id and task.user_id and task.user_id != user_id:
                return False
            task.status = "pending"
            task.retry_count = 0
            task.next_run_at = datetime.now()
            task.next_retry_at = None
            task.last_error = None
            await session.flush()
            log.info("DLQ task retried: id={} by user={}", task_id, user_id or "admin")
            return True

    @staticmethod
    def _to_dict(t: ScheduledTaskORM) -> dict:
        return {
            "id": t.id, "message": t.message, "description": t.description,
            "run_at": t.run_at.isoformat() if t.run_at else None,
            "recurring": t.recurring,
            "cron_expr": t.cron_expr,
            "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
            "status": t.status, "channel": t.channel,
            "chat_id": t.chat_id, "user_id": t.user_id,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "retry_count": t.retry_count, "max_retries": t.max_retries,
            "next_retry_at": t.next_retry_at.isoformat() if t.next_retry_at else None,
            "last_error": t.last_error,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
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
        """触发所有 next_run_at <= now 的 pending 任务

        PostgreSQL 用 SELECT FOR UPDATE SKIP LOCKED 多实例防重；
        SQLite 单机无需 SKIP LOCKED。
        """
        now = datetime.now()
        async with get_db_session() as session:
            stmt = (
                select(ScheduledTaskORM)
                .where(
                    ScheduledTaskORM.status == "pending",
                    ScheduledTaskORM.next_run_at <= now,
                )
                .order_by(ScheduledTaskORM.next_run_at.asc())
                .limit(50)
            )
            # PostgreSQL 多实例防重
            if settings.db_backend == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
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
            asyncio.create_task(self._execute_one(
                t.id, t.message, t.channel, t.chat_id, t.user_id,
                t.context, t.recurring, t.cron_expr,
            ))

    async def _execute_one(
        self, task_id: int, message: str, channel: str,
        chat_id: Optional[str], user_id: Optional[str],
        context: dict, recurring: str, cron_expr: Optional[str],
    ) -> None:
        """执行单个定时任务（调用 AgentService）

        失败时调用 _handle_failure 进行重试或入 DLQ。
        """
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
            await self._handle_failure(task_id, {"code": "EXEC_ERROR", "message": str(e)})
            return

        # 更新状态：周期性 → 计算下次执行；一次性 → completed
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is None:
                return
            if cron_expr:
                # cron 任务：用 croniter 计算下次
                nxt = _cron_next(cron_expr)
                if nxt:
                    task.status = "pending"
                    task.next_run_at = nxt
                else:
                    task.status = "completed"
            elif recurring == "none":
                task.status = "completed"
            else:
                task.status = "pending"
                task.next_run_at = self._compute_next_run(recurring)
            await session.flush()

    async def _handle_failure(self, task_id: int, error: dict) -> None:
        """失败处理：重试（指数退避）或进入 DLQ

        - retry_count += 1
        - 若 < max_retries：状态回 pending，next_retry_at = now + 2^retry_count * 60s
        - 若 >= max_retries：状态 failed，进入 DLQ
        """
        async with get_db_session() as session:
            task = await session.get(ScheduledTaskORM, task_id)
            if task is None:
                return
            task.retry_count = (task.retry_count or 0) + 1
            task.last_error = error
            max_r = task.max_retries or _DEFAULT_MAX_RETRIES
            if task.retry_count < max_r:
                # 重试：状态回 pending，指数退避
                backoff = (2 ** task.retry_count) * 60  # 2min, 4min, 8min...
                task.status = "pending"
                task.next_run_at = datetime.now() + timedelta(seconds=backoff)
                task.next_retry_at = task.next_run_at
                log.warning(
                    "Task {} retry {}/{} backoff={}s",
                    task_id, task.retry_count, max_r, backoff,
                )
            else:
                # 进入 DLQ
                task.status = "failed"
                log.error(
                    "Task {} entered DLQ after {} retries: {}",
                    task_id, task.retry_count, error,
                )
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
