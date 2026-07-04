"""HTTP 指标中间件 - 采集请求计数/延迟到 Prometheus

注册在 CORSMiddleware 之后（外层），记录所有 HTTP 请求的：
- method/path/status 维度的请求计数
- method/path 维度的延迟直方图

注意：路径参数需归一化（/tasks/{id} 而非 /tasks/具体ID），避免高基数。
"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.metrics import record_http_request


class MetricsMiddleware(BaseHTTPMiddleware):
    """采集 HTTP 请求指标到 Prometheus"""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            duration = time.perf_counter() - start
            # 路径归一化：将 /tasks/abc-123 → /tasks/{id}，避免高基数
            path = _normalize_path(request.url.path)
            record_http_request(request.method, path, status, duration)


def _normalize_path(path: str) -> str:
    """归一化路径，将动态 ID 替换为 {id}，避免 Prometheus 标签高基数"""
    # 健康检查/指标端点保留原样
    if path in ("/health", "/ready", "/metrics", "/", "/docs", "/redoc", "/openapi.json"):
        return path
    parts = path.split("/")
    normalized: list[str] = []
    for p in parts:
        if not p:
            normalized.append("")
            continue
        # UUID（36字符含-）或长数字ID → {id}
        if len(p) >= 8 and "-" in p:
            normalized.append("{id}")
        elif p.isdigit():
            normalized.append("{id}")
        else:
            normalized.append(p)
    return "/".join(normalized)
