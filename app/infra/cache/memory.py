"""MemoryCache - 进程内缓存实现（单机/小企业部署，零外部依赖）

特性：
- 字典 + 过期时间戳，单进程内有效（重启丢失）
- asyncio.Lock 保证并发安全
- 适合开发环境和小规模单机部署
- 不支持分布式锁语义（多实例部署需用 RedisCache）

实现 ICache 协议（app.domain.contracts.cache.ICache）。
"""
import asyncio
import time
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("memory_cache")


class MemoryCache:
    """进程内缓存（结构化满足 ICache Protocol）"""

    def __init__(self) -> None:
        # key -> (value, expire_at_seconds)
        self._store: dict[str, tuple[str, float]] = {}
        # rate_limit 滑动窗口：key -> [timestamp,...]
        self._rate_buckets: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if expire_at < time.time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        async with self._lock:
            expire_at = time.time() + max(ttl, 1)
            self._store[key] = (value, expire_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def acquire_lock(self, key: str, ttl: int = 30) -> bool:
        """单机锁：通过 SET NX 语义模拟"""
        async with self._lock:
            now = time.time()
            entry = self._store.get(key)
            if entry and entry[1] > now:
                return False
            self._store[key] = ("1", now + ttl)
            return True

    async def release_lock(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def rate_limit(self, key: str, limit: int, window: int = 1) -> bool:
        """滑动窗口限流"""
        now = time.time()
        async with self._lock:
            bucket = self._rate_buckets.setdefault(key, [])
            # 清理过期时间戳
            cutoff = now - window
            bucket[:] = [t for t in bucket if t > cutoff]
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    async def ping(self) -> bool:
        return True

    async def cleanup_expired(self) -> int:
        """清理过期 key（可选，由后台任务定期调用）"""
        now = time.time()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if exp < now]
            for k in expired:
                del self._store[k]
            return len(expired)
