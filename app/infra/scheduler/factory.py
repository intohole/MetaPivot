"""Scheduler 工厂 - 按 scheduler_backend 配置切换实现

部署场景：
- async（默认）：AsyncScheduler（asyncio + DB 轮询，单进程零外部依赖）
- celery（预留）：CeleryScheduler（基于 celery-beat，集群多实例）

接口：IScheduler Protocol，调用方无感知
"""
from typing import Optional

from app.domain.contracts.scheduler import IScheduler
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("scheduler_factory")

_scheduler: Optional[IScheduler] = None


async def get_scheduler() -> IScheduler:
    """获取全局 Scheduler 实例（单例）"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    backend = settings.scheduler_backend
    if backend == "async":
        from app.infra.scheduler.async_scheduler import AsyncScheduler
        _scheduler = AsyncScheduler()
    # elif backend == "celery":
    #     from app.infra.scheduler.celery_scheduler import CeleryScheduler
    #     _scheduler = CeleryScheduler()
    else:
        log.warning("Unknown scheduler_backend '{}', fallback to async", backend)
        from app.infra.scheduler.async_scheduler import AsyncScheduler
        _scheduler = AsyncScheduler()

    log.info("Scheduler initialized: backend={}", backend)
    return _scheduler


async def close_scheduler() -> None:
    """关闭 Scheduler（lifespan shutdown 调用）"""
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
        log.info("Scheduler closed")


async def check_scheduler_health() -> bool:
    """健康检查"""
    if _scheduler is None:
        return False
    try:
        return await _scheduler.health()
    except Exception as e:
        log.warning("scheduler health check failed: {}", e)
        return False
