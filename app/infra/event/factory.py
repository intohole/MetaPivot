"""事件总线工厂 - 按配置切换 Local/Redis backend

设计：
- 与 cache/vector factory 保持一致风格
- 跟随 CACHE_BACKEND：memory → Local，redis → Redis
- 单例模式，避免重复初始化
- close_event_bus() 在应用关闭时调用
"""
from typing import Optional

from app.domain.contracts.event_bus import IEventBus
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("event_factory")

_bus: Optional[IEventBus] = None


def get_event_bus() -> IEventBus:
    """获取事件总线单例（按配置切换 backend）

    - CACHE_BACKEND=memory → LocalEventBus（进程内，零依赖）
    - CACHE_BACKEND=redis  → RedisEventBus（Pub/Sub 跨实例广播）
    """
    global _bus
    if _bus is not None:
        return _bus

    if settings.cache_backend == "redis":
        from app.infra.event.redis_bus import RedisEventBus
        _bus = RedisEventBus()
        log.info("EventBus backend: redis (cluster mode)")
    else:
        from app.infra.event.local_bus import LocalEventBus
        _bus = LocalEventBus()
        log.info("EventBus backend: local (single-process)")

    return _bus


async def init_event_bus() -> IEventBus:
    """初始化事件总线（启动后台监听等）"""
    bus = get_event_bus()
    await bus.init()
    return bus


async def close_event_bus() -> None:
    """关闭事件总线"""
    global _bus
    if _bus is not None:
        await _bus.close()
        _bus = None
        log.info("EventBus closed")


async def check_event_bus_health() -> bool:
    """健康检查（通过 IEventBus.health() 实际探测连接活性）"""
    if _bus is None:
        return False
    try:
        return await _bus.health()
    except Exception as e:
        log.error("EventBus health check failed: {}", e)
        return False
