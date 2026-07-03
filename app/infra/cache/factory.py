"""缓存后端工厂 - 按 settings.cache_backend 切换

支持部署规模：
- memory：进程内字典（小企业/开发环境，零外部依赖）
- redis：分布式缓存（多实例/大型企业）

返回的实例结构化满足 ICache Protocol，调用方无需关心具体实现。
"""
from typing import Optional

from app.domain.contracts.cache import ICache
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("cache_factory")

_cache: Optional[ICache] = None


async def get_cache() -> ICache:
    """获取缓存单例（首次调用时按配置初始化）"""
    global _cache
    if _cache is None:
        if settings.cache_backend == "redis":
            from app.infra.cache.redis_cache import RedisCache
            _cache = await RedisCache().init()
            log.info("Cache backend: redis")
        else:
            from app.infra.cache.memory import MemoryCache
            _cache = MemoryCache()
            log.info("Cache backend: memory")
    return _cache


async def close_cache() -> None:
    """关闭缓存连接（应用关闭时调用）"""
    global _cache
    if _cache is not None:
        # RedisCache 有 close 方法；MemoryCache 无需释放
        close_method = getattr(_cache, "close", None)
        if close_method is not None:
            await close_method()
        _cache = None
        log.info("Cache closed")


async def check_cache_health() -> bool:
    """缓存健康检查"""
    try:
        cache = await get_cache()
        return await cache.ping()
    except Exception as e:
        log.error("Cache health check failed: {}", e)
        return False
