"""RedisCache - Redis 分布式缓存实现（多实例/大型企业部署）

特性：
- 分布式缓存，跨进程共享
- 支持 SET NX 分布式锁
- 令牌桶限流（pipeline 原子操作）
- 适合多实例部署、超大型企业

实现 ICache 协议（app.domain.contracts.cache.ICache）。
"""
from typing import Optional

import redis.asyncio as aioredis

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("redis")


class RedisCache:
    """Redis 缓存实现（结构化满足 ICache Protocol）"""

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    async def init(self) -> "RedisCache":
        """初始化连接池（幂等）"""
        if self._client is None:
            self._client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            try:
                await self._client.ping()
                log.info("Redis connected: {}", settings.redis_host)
            except Exception as e:
                log.error("Redis connection failed: {}", e)
                raise
        return self

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("Redis connection closed")

    def _get(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("RedisCache not initialized, call init() first")
        return self._client

    async def get(self, key: str) -> Optional[str]:
        return await self._get().get(key)

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        await self._get().setex(key, ttl, value)

    async def delete(self, key: str) -> None:
        await self._get().delete(key)

    async def acquire_lock(self, key: str, ttl: int = 30) -> bool:
        """SET NX EX 分布式锁"""
        return bool(await self._get().set(key, "1", nx=True, ex=ttl))

    async def release_lock(self, key: str) -> None:
        await self._get().delete(key)

    async def rate_limit(self, key: str, limit: int, window: int = 1) -> bool:
        """令牌桶限流：INCR + EXPIRE pipeline 原子操作"""
        client = self._get()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        results = await pipe.execute()
        return results[0] <= limit

    async def ping(self) -> bool:
        try:
            await self._get().ping()
            return True
        except Exception as e:
            log.error("Redis ping failed: {}", e)
            return False
