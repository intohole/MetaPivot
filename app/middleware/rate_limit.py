"""限流中间件 - Redis 令牌桶

策略：
- /api/v1/im/* 走 IM 限流（IM_RATE_LIMIT_QPS，默认 20）
- 其他 /api/* 走 API 限流（API_RATE_LIMIT_QPS，默认 60）
- /health /ready /docs /openapi.json 不限流
- 超限返回 429 + Retry-After

依赖：infra/cache/redis_client（Data 层）
"""
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.infra.cache.redis_client import rate_limit
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("rate_limit")

# 不限流路径前缀
_EXEMPT_PATHS = ("/health", "/ready", "/docs", "/redoc", "/openapi.json")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis 的令牌桶限流中间件"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 健康检查与文档路径放行
        if path.startswith(_EXEMPT_PATHS):
            return await call_next(request)

        if not path.startswith("/api/"):
            return await call_next(request)

        # 区分 IM 回调与普通 API
        if path.startswith("/api/v1/im/"):
            limit = settings.rate_limit_im_qps
            bucket = "im"
        else:
            limit = settings.rate_limit_api_qps
            bucket = "api"

        # 限流 key：bucket + 客户端IP（按 IP 维度限流）
        client_ip = request.client.host if request.client else "unknown"
        key = f"rl:{bucket}:{client_ip}:{path}"

        try:
            allowed = await rate_limit(key, limit=limit, window=1)
        except Exception as e:
            # Redis 故障时降级为不限流（避免拖垮服务）
            log.warning("Rate limit check failed (degraded): {}", e)
            allowed = True

        if not allowed:
            log.warning("Rate limited: {} {} (limit={}/s)", client_ip, path, limit)
            return JSONResponse(
                status_code=429,
                content={
                    "code": "RATE_LIMITED",
                    "message": f"请求过于频繁，限流 {limit}/秒",
                    "retry_after": 1,
                },
                headers={"Retry-After": "1"},
            )

        response: Response = await call_next(request)
        return response
