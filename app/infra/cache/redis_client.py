"""Redis 异步连接管理"""
from typing import Optional

import redis.asyncio as aioredis

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("redis")

_redis_pool: Optional[aioredis.Redis] = None


async def init_redis() -> aioredis.Redis:
    """初始化Redis连接池"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        try:
            await _redis_pool.ping()
            log.info("Redis connected: {}", settings.redis_host)
        except Exception as e:
            log.error("Redis connection failed: {}", e)
            raise
    return _redis_pool


async def close_redis() -> None:
    """关闭Redis连接"""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.close()
        _redis_pool = None
        log.info("Redis connection closed")


def get_redis() -> aioredis.Redis:
    """获取Redis客户端"""
    if _redis_pool is None:
        raise RuntimeError("Redis not initialized, call init_redis() first")
    return _redis_pool


async def check_redis_health() -> bool:
    """Redis健康检查"""
    try:
        redis_client = get_redis()
        await redis_client.ping()
        return True
    except Exception as e:
        log.error("Redis health check failed: {}", e)
        return False


# ============ 便捷工具函数 ============

async def cache_set(key: str, value: str, ttl: int = 3600) -> None:
    """设置缓存"""
    redis_client = get_redis()
    await redis_client.setex(key, ttl, value)


async def cache_get(key: str) -> Optional[str]:
    """获取缓存"""
    redis_client = get_redis()
    return await redis_client.get(key)


async def cache_delete(key: str) -> None:
    """删除缓存"""
    redis_client = get_redis()
    await redis_client.delete(key)


async def acquire_lock(key: str, ttl: int = 30) -> bool:
    """获取分布式锁（NX模式）"""
    redis_client = get_redis()
    return bool(await redis_client.set(key, "1", nx=True, ex=ttl))


async def release_lock(key: str) -> None:
    """释放锁"""
    redis_client = get_redis()
    await redis_client.delete(key)


async def rate_limit(key: str, limit: int, window: int = 1) -> bool:
    """令牌桶限流：返回True表示允许"""
    redis_client = get_redis()
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    results = await pipe.execute()
    return results[0] <= limit
