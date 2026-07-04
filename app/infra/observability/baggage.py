"""Baggage 传播 - 跨任务上下文关联

设计：
- attach_user_baggage：在 asyncio.create_task 边界显式 attach（contextvars 不跨 task）
- BaggageSpanProcessor：on_start 时从 baggage 读取 user.id/session.id/metapivot.task_id 写入 span
- Baggage 是 OTel 标准跨进程传播机制，HTTP 中间件自动 attach/detach
"""
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("observability.baggage")

_BAGGAGE_KEYS = ("user.id", "session.id", "metapivot.task_id")


def attach_user_baggage(
    user_id: str, chat_id: str = "", task_id: str = ""
) -> Optional[object]:
    """设置 Baggage（user.id / session.id / metapivot.task_id）

    Returns:
        Token 用于 detach；OTel 不可用时返回 None
    """
    try:
        from opentelemetry import baggage as otel_baggage
        ctx = otel_baggage.set_baggage("user.id", user_id or "")
        ctx = otel_baggage.set_baggage("session.id", chat_id or "", context=ctx)
        ctx = otel_baggage.set_baggage("metapivot.task_id", task_id or "", context=ctx)
        token = otel_baggage.attach(ctx)
        log.debug("Baggage attached: user={} task={}", user_id, task_id)
        return token
    except ImportError:
        return None
    except Exception as e:
        log.warning("attach_user_baggage failed: {}", e)
        return None


def detach_user_baggage(token: Optional[object]) -> None:
    """分离 Baggage（与 attach 配对）"""
    if token is None:
        return
    try:
        from opentelemetry import baggage as otel_baggage
        otel_baggage.detach(token)
    except Exception as e:
        log.warning("detach_user_baggage failed: {}", e)


class BaggageSpanProcessor:
    """Span 处理器：on_start 时从 baggage 读取属性写入 span

    实现 OTel SpanProcessor 接口（仅 on_start，on_end 由 BatchSpanProcessor 处理）。
    用于将 user.id/session.id/metapivot.task_id 从 baggage 注入 span 属性，
    实现 Langfuse UI 中按 user/session 过滤 span。
    """

    def on_start(self, span, parent_context=None):
        try:
            from opentelemetry import baggage as otel_baggage
            for key in _BAGGAGE_KEYS:
                value = otel_baggage.get_baggage(key, context=parent_context)
                if value:
                    span.set_attribute(key, value)
        except Exception as e:
            log.debug("BaggageSpanProcessor on_start failed: {}", e)

    def on_end(self, span):
        pass  # 由 BatchSpanProcessor 处理 export

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
