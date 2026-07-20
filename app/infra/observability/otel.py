"""OTel SDK 初始化 + Langfuse OTLP exporter

设计：
- settings.otel_enabled=False 时返回 NoopTracer（默认，零依赖）
- settings.otel_enabled=True 时初始化 OTel SDK + BaggageSpanProcessor + OTLP exporter
- Langfuse 通过 OTLP endpoint 接收 span（无需 SDK helpers，单一采集路径）
- BatchSpanProcessor 异步批量上报，不阻塞主链路
"""
from typing import Any

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("observability.otel")

_tracer: Any = None
_initialized: bool = False


class NoopTracer:
    """空 tracer 实现（otel_enabled=False 时使用）

    所有 span 操作为 no-op，零开销。
    span_helper.py 在 _get_tracer() 中检查 isinstance(tracer, NoopTracer) 返回 None，
    所以 NoopTracer.start_as_current_span 实际不会被调用。
    """

    def start_as_current_span(self, name: str, **kwargs):
        from contextlib import nullcontext
        return nullcontext()


def init_otel() -> None:
    """初始化 OTel SDK（lifespan startup 调用）

    settings.otel_enabled=False 时跳过，使用 NoopTracer。
    """
    global _tracer, _initialized
    if _initialized:
        return
    _initialized = True

    if not settings.otel_enabled:
        _tracer = NoopTracer()
        log.info("OTel disabled, using NoopTracer")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from app.infra.observability.baggage import BaggageSpanProcessor

        resource = Resource.create({
            "service.name": settings.app_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.app_env,
        })
        provider = TracerProvider(resource=resource)

        # BaggageSpanProcessor：从 baggage 读取 user.id/session.id 写入 span 属性
        provider.add_span_processor(BaggageSpanProcessor())

        # Langfuse OTLP exporter（异步批量上报，不阻塞主链路）
        if settings.langfuse_enabled and settings.langfuse_host:
            exporter = _build_langfuse_exporter()
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("metapivot.agent")
        log.info("OTel initialized with Langfuse exporter: {}", settings.langfuse_host)
    except Exception as e:
        log.warning("OTel init failed, fallback to NoopTracer: {}", e)
        _tracer = NoopTracer()


def _build_langfuse_exporter():
    """构造 Langfuse OTLP exporter（带 Basic Auth）"""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    import base64

    # Langfuse OTLP endpoint: https://{host}/api/public/otel
    endpoint = f"{settings.langfuse_host.rstrip('/')}/api/public/otel"
    auth_str = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}",
    }
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)


def get_tracer() -> Any:
    """获取 tracer 实例（懒初始化，未 init 时返回 NoopTracer）"""
    if _tracer is None:
        return NoopTracer()
    return _tracer


def shutdown_otel() -> None:
    """关闭 OTel SDK，flush 待上报数据（lifespan shutdown 调用）"""
    global _tracer, _initialized
    if not _initialized or isinstance(_tracer, NoopTracer):
        return
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
        if hasattr(provider, "shutdown"):
            provider.shutdown()
        log.info("OTel shutdown complete")
    except Exception as e:
        log.warning("OTel shutdown failed: {}", e)
    finally:
        _tracer = None
        _initialized = False
