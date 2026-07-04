"""ITracer - 链路追踪抽象接口

支持的实现：
- NoopTracer：空实现，otel_enabled=False 时使用（默认，零依赖）
- OTelTracer：OpenTelemetry SDK 实现，配合 Langfuse OTLP exporter

接口约束：
- get_tracer() 返回 tracer 对象（contextmanager 使用）
- init/shutdown 生命周期管理
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ITracer(Protocol):
    """链路追踪后端统一接口（Phase 4 OTel + Langfuse 可观测性）"""

    def get_tracer(self) -> Any:
        """获取 tracer 实例（用于 start_as_current_span 等）"""
        ...

    def init(self) -> None:
        """初始化追踪 SDK（lifespan startup 调用）"""
        ...

    def shutdown(self) -> None:
        """关闭追踪 SDK，flush 待上报数据（lifespan shutdown 调用）"""
        ...
