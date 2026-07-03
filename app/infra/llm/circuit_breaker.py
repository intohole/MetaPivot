"""LLM 熔断器 - 连续失败降级保护

模式：
- CLOSED（正常）：请求通过，记录失败次数
- OPEN（熔断）：连续失败 ≥ threshold 次，直接拒绝请求 duration 秒
- HALF_OPEN（半开）：熔断超时后放行一次试探请求，成功则恢复，失败则重新熔断

基于 ICache 实现（Memory/Redis 均可），不引入新依赖。
跨实例共享状态（Redis 模式下各实例看到同一熔断状态）。
"""
import time
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("circuit_breaker")

# 熔断参数
_FAILURE_THRESHOLD = 5        # 连续失败 5 次触发熔断
_OPEN_DURATION = 30           # 熔断 30 秒
_CACHE_KEY_FAIL = "llm:circuit:failures"
_CACHE_KEY_OPEN = "llm:circuit:open_until"


class CircuitBreaker:
    """LLM 熔断器（基于 ICache）"""

    def __init__(self, cache_getter) -> None:
        """cache_getter: async callable 返回 ICache 实例"""
        self._get_cache = cache_getter

    async def allow_request(self) -> tuple[bool, str]:
        """检查是否允许调用 LLM

        Returns:
            (allowed, reason)
        """
        cache = await self._get_cache()
        open_until_str = await cache.get(_CACHE_KEY_OPEN)
        if open_until_str:
            open_until = float(open_until_str)
            if time.time() < open_until:
                remaining = int(open_until - time.time())
                return False, f"circuit_open({remaining}s remaining)"
            # 熔断超时 → 半开状态，放行一次
            await cache.delete(_CACHE_KEY_OPEN)
            log.info("Circuit breaker half-open, probing request")
        return True, "ok"

    async def record_success(self) -> None:
        """记录成功：重置失败计数"""
        cache = await self._get_cache()
        await cache.delete(_CACHE_KEY_FAIL)

    async def record_failure(self) -> None:
        """记录失败：达到阈值则熔断"""
        cache = await self._get_cache()
        fail_str = await cache.get(_CACHE_KEY_FAIL)
        failures = int(fail_str) + 1 if fail_str else 1
        await cache.set(_CACHE_KEY_FAIL, str(failures), ttl=300)

        if failures >= _FAILURE_THRESHOLD:
            # 触发熔断
            open_until = time.time() + _OPEN_DURATION
            await cache.set(_CACHE_KEY_OPEN, str(open_until), ttl=_OPEN_DURATION + 5)
            log.error(
                "Circuit breaker OPENED: {} consecutive failures, blocking for {}s",
                failures, _OPEN_DURATION,
            )
        else:
            log.warning("LLM failure recorded: {}/{}", failures, _FAILURE_THRESHOLD)

    async def state(self) -> str:
        """查询当前状态（用于健康检查）"""
        cache = await self._get_cache()
        open_until_str = await cache.get(_CACHE_KEY_OPEN)
        if open_until_str and time.time() < float(open_until_str):
            return f"open({int(float(open_until_str) - time.time())}s)"
        fail_str = await cache.get(_CACHE_KEY_FAIL)
        if fail_str:
            return f"closed({fail_str} recent failures)"
        return "closed(healthy)"


# 单例
_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取熔断器单例"""
    global _breaker
    if _breaker is None:
        from app.infra.cache.factory import get_cache
        _breaker = CircuitBreaker(cache_getter=get_cache)
    return _breaker
