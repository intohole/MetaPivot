"""Redis 客户端 - 向后兼容 shim

⚠️ 此模块为兼容旧代码保留，内部委托给 ICache（按配置切换 MemoryCache/RedisCache）。
新代码请直接使用：
    from app.infra.cache.factory import get_cache
    cache = await get_cache()
    await cache.set(key, value, ttl)

保留的函数签名与原 redis_client 完全一致，确保现有调用方零改动。
"""
from typing import Optional

from app.infra.cache.factory import check_cache_health, close_cache, get_cache


async def init_redis():
    """初始化缓存（按配置选择 memory/redis backend）"""
    await get_cache()


async def close_redis():
    """关闭缓存连接"""
    await close_cache()


async def check_redis_health() -> bool:
    """缓存健康检查（保留旧名）"""
    return await check_cache_health()


async def cache_set(key: str, value: str, ttl: int = 3600) -> None:
    cache = await get_cache()
    await cache.set(key, value, ttl)


async def cache_get(key: str) -> Optional[str]:
    cache = await get_cache()
    return await cache.get(key)


async def cache_delete(key: str) -> None:
    cache = await get_cache()
    await cache.delete(key)


async def acquire_lock(key: str, ttl: int = 30) -> bool:
    cache = await get_cache()
    return await cache.acquire_lock(key, ttl)


async def release_lock(key: str) -> None:
    cache = await get_cache()
    await cache.release_lock(key)


async def rate_limit(key: str, limit: int, window: int = 1) -> bool:
    cache = await get_cache()
    return await cache.rate_limit(key, limit, window)


def get_redis():
    """兼容旧 API：返回具有 ping/get/set 等方法的对象

    ⚠️ 仅用于无法改造的第三方库；新代码请用 get_cache()。
    """
    raise RuntimeError(
        "get_redis() 已弃用，请使用 'from app.infra.cache.factory import get_cache' "
        "并调用 'await get_cache()' 获取 ICache 实例"
    )
