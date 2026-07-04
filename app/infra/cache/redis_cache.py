"""RedisCache - Redis 分布式缓存实现（多实例/大型企业部署）

特性：
- 分布式缓存，跨进程共享
- 支持 SET NX 分布式锁
- 令牌桶限流（pipeline 原子操作）
- 适合多实例部署、超大型企业

实现 ICache 协议（app.domain.contracts.cache.ICache）。
"""
from typing import Optional, Tuple

import redis.asyncio as aioredis

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("redis")


# Lua 令牌桶脚本：原子 refill + consume + retry_after 计算
# keys[1]=限流key, argv[1]=capacity, argv[2]=refill_rate(令牌/秒), argv[3]=now_ms, argv[4]=cost
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- 按时间间隔补充令牌
local elapsed = math.max(0, now - last_refill) / 1000.0
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
else
    -- 计算等待时间（秒）：缺多少令牌 / 补充速率
    retry_after = math.ceil((cost - tokens) / refill_rate)
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) + 1)
return {allowed, retry_after}
"""


class RedisCache:
    """Redis 缓存实现（结构化满足 ICache Protocol）"""

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None
        self._token_bucket_script = None  # Lua 令牌桶脚本（init 时注册）

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
                # 注册 Lua 令牌桶脚本（EVALSHA 缓存，避免重复传输脚本体）
                self._token_bucket_script = self._client.register_script(_TOKEN_BUCKET_LUA)
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

    async def rate_limit(self, key: str, limit: int, window: int = 1) -> Tuple[bool, int]:
        """令牌桶限流：Lua 原子操作，返回 (allowed, retry_after)

        capacity=limit, refill_rate=limit/window（每秒补充 limit/window 个令牌）。
        替代 INCR+EXPIRE 固定窗口，避免边界突发 2x 问题。
        """
        if self._token_bucket_script is None:
            raise RuntimeError("Token bucket script not registered, call init() first")
        import time
        now_ms = int(time.time() * 1000)
        refill_rate = limit / max(window, 1)
        result = await self._token_bucket_script(
            keys=[key], args=[limit, refill_rate, now_ms, 1]
        )
        return bool(result[0]), int(result[1])

    async def ping(self) -> bool:
        try:
            await self._get().ping()
            return True
        except Exception as e:
            log.error("Redis ping failed: {}", e)
            return False
