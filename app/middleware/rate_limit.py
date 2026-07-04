"""限流中间件 - 用户/JWT 维度 + 动态 Retry-After

策略：
- /api/v1/im/* 走 IM 限流（IM_RATE_LIMIT_QPS，默认 20）
- 其他 /api/* 走 API 限流（API_RATE_LIMIT_QPS，默认 60）
- /health /ready /docs /openapi.json 不限流
- 限流维度：优先 user:{jwt_sub}，无 token 走 ip:{client_ip}
- 超限返回 429 + 动态 Retry-After（秒）

依赖：infra/cache/redis_client（Data 层）
"""
import jwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.infra.cache.redis_client import rate_limit
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("rate_limit")

# 不限流路径前缀
_EXEMPT_PATHS = ("/health", "/ready", "/docs", "/redoc", "/openapi.json")


def _extract_user_id(request: Request) -> str:
    """从 Bearer token 提取 sub（仅分桶用，不校验签名）

    限流分桶目的：避免同 IP 多账号绕过限流。
    签名校验由 Auth 依赖负责，此处仅解码 payload 取 sub。
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    token = auth[7:]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return str(payload.get("sub", ""))
    except Exception:
        return ""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """用户维度 + 动态 Retry-After 限流中间件"""

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

        # 限流维度：优先 user:{jwt_sub}，无 token 走 ip:{client_ip}
        client_ip = request.client.host if request.client else "unknown"
        user_id = _extract_user_id(request)
        identity = f"user:{user_id}" if user_id else f"ip:{client_ip}"
        key = f"rl:{bucket}:{identity}:{path}"

        try:
            allowed, retry_after = await rate_limit(key, limit=limit, window=1)
        except Exception as e:
            # 缓存故障时降级为不限流（避免拖垮服务）
            log.warning("Rate limit check failed (degraded): {}", e)
            allowed, retry_after = True, 0

        if not allowed:
            log.warning("Rate limited: {} {} (limit={}/s, retry_after={}s)",
                        identity, path, limit, retry_after)
            return JSONResponse(
                status_code=429,
                content={
                    "code": "RATE_LIMITED",
                    "message": f"请求过于频繁，限流 {limit}/秒",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        response: Response = await call_next(request)
        return response
