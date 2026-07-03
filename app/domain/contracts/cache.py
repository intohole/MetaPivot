"""ICache - 缓存抽象接口

支持的实现：
- MemoryCache：进程内字典 + TTL，适合单机/小企业部署（无需 Redis）
- RedisCache：分布式缓存，适合多实例/超大型企业

接口约束：
- 所有方法异步，禁止阻塞主线程
- key/value 均为字符串（与现有 redis_client 函数签名保持一致）
- TTL 单位为秒；0 或负数表示不缓存
"""
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ICache(Protocol):
    """缓存后端统一接口"""

    async def get(self, key: str) -> Optional[str]:
        """读取缓存，不存在返回 None"""
        ...

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        """写入缓存（带 TTL，秒）"""
        ...

    async def delete(self, key: str) -> None:
        """删除缓存"""
        ...

    async def acquire_lock(self, key: str, ttl: int = 30) -> bool:
        """获取分布式锁（NX 语义），返回 True 表示获取成功"""
        ...

    async def release_lock(self, key: str) -> None:
        """释放锁"""
        ...

    async def rate_limit(self, key: str, limit: int, window: int = 1) -> bool:
        """令牌桶限流：window 秒内允许 limit 次请求，返回 True 放行"""
        ...

    async def ping(self) -> bool:
        """健康检查"""
        ...
