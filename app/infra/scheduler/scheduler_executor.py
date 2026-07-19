"""定时任务执行器 — 单任务执行 + 失败处理 + IM 推送

Sprint 8.1: 从 async_scheduler.py 拆离，保持 async_scheduler.py ≤ 300 行。
职责：
- execute_one: 执行单个定时任务（调用 AgentService + 更新状态）
- handle_failure: 失败重试（指数退避）或入 DLQ
- _push_result_to_im: IM 渠道结果推送（fire-and-forget）

设计：模块级函数，_compute_next_run 从 async_scheduler 导入复用。
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from app.domain.agent.cron_helper import next_run_at as _cron_next
from app.infra.db.models_core import ScheduledTaskORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("scheduler_executor")

# 单次执行超时（秒）— 仅保护调度循环，不限制 Agent 任务本身
_TRIGGER_TIMEOUT = 10
# 默认最大重试次数（DB 字段缺省时的兜底）
_DEFAULT_MAX_RETRIES = 3


async def execute_one(
    task_id: int,
    message: str,
    channel: str,
    chat_id: Optional[str],
    user_id: Optional[str],
    context: dict,
    recurring: str,
    cron_expr: Optional[str],
) -> None:
    """执行单个定时任务（调用 AgentService）

    失败时调用 handle_failure 进行重试或入 DLQ。
    Sprint 6.4: IM 渠道任务触发后异步推送结果到原会话（fire-and-forget）。
    Sprint 8.1: 从 AsyncScheduler._execute_one 迁移为模块级函数。
    """
    from app.infra.scheduler.async_scheduler import compute_next_run

    agent_task_id = ""
    try:
        from app.service.agent_service import agent_service
        result = await asyncio.wait_for(
            agent_service.start_task(
                message=message, channel=channel,
                chat_id=chat_id or "", user_id=user_id or "",
                context=context or {}, stream=False,
            ),
            timeout=_TRIGGER_TIMEOUT,
        )
        agent_task_id = result.get("task_id", "") if isinstance(result, dict) else ""
        log.info("Scheduled task executed: id={} agent_task={}", task_id, agent_task_id)
    except Exception as e:
        log.exception("Scheduled task execution failed: id={} err={}", task_id, e)
        await handle_failure(task_id, {"code": "EXEC_ERROR", "message": str(e)})
        return

    # Sprint 6.4: IM 渠道的定时任务 → 异步推送 Agent 结果到原会话
    if agent_task_id and chat_id and channel and channel != "api":
        asyncio.create_task(_push_result_to_im(agent_task_id, channel, chat_id, message))

    # 更新状态：周期性 → 计算下次执行；一次性 → completed
    async with get_db_session() as session:
        task = await session.get(ScheduledTaskORM, task_id)
        if task is None:
            return
        if cron_expr:
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
            task.next_run_at = compute_next_run(recurring)
        await session.flush()


async def _push_result_to_im(
    agent_task_id: str, channel: str, chat_id: str, message: str,
) -> None:
    """Sprint 6.4: 异步推送 Agent 结果到 IM（fire-and-forget 包装）"""
    try:
        from app.service.im_push_service import im_push_service
        await im_push_service.push_agent_result(
            task_id=agent_task_id, channel=channel,
            chat_id=chat_id, trigger_message=message,
        )
    except Exception as e:
        log.warning("IM push for agent task {} failed: {}", agent_task_id, e)


async def handle_failure(task_id: int, error: dict) -> None:
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
            backoff = (2 ** task.retry_count) * 60  # 2min, 4min, 8min...
            task.status = "pending"
            task.next_run_at = datetime.now() + timedelta(seconds=backoff)
            task.next_retry_at = task.next_run_at
            log.warning(
                "Task {} retry {}/{} backoff={}s",
                task_id, task.retry_count, max_r, backoff,
            )
        else:
            task.status = "failed"
            log.error(
                "Task {} entered DLQ after {} retries: {}",
                task_id, task.retry_count, error,
            )
            await session.flush()