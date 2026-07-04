"""Span contextmanager - 包装 LLM/Tool/Agent step 调用

设计：
- 三层 span：trace(session) → span(agent_step) → span(tool_call) → span(llm_call)
- 使用 OTel GenAI semantic conventions（gen_ai.operation.name / gen_ai.system / gen_ai.request.model）
- 异常时记录到 span event，不阻断主流程
- PII 脱敏：span event 内调 sanitize_output 后再 set_attribute
"""
import json
from contextlib import contextmanager
from typing import Any

from app.utils.context import get_request_id
from app.utils.logger import get_logger

log = get_logger("observability.span")


@contextmanager
def llm_span(model: str, messages: list, tools_count: int = 0):
    """LLM 调用 span（gen_ai.operation.name=chat）"""
    tracer = _get_tracer()
    if tracer is None:
        yield _NoopSpan()
        return
    with tracer.start_as_current_span("llm.chat_completion") as span:
        try:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", "openai")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("metapivot.tools_count", tools_count)
            span.set_attribute("metapivot.request_id", get_request_id())
            prompt_summary = _summarize_messages(messages)
            if prompt_summary:
                span.set_attribute("metapivot.prompt_summary", prompt_summary)
            yield span
        except Exception as e:
            span.record_exception(e)
            raise


@contextmanager
def tool_span(tool_name: str, args: dict):
    """工具调用 span"""
    tracer = _get_tracer()
    if tracer is None:
        yield _NoopSpan()
        return
    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        try:
            span.set_attribute("metapivot.tool.name", tool_name)
            span.set_attribute("metapivot.request_id", get_request_id())
            args_summary = _summarize_args(args)
            if args_summary:
                span.set_attribute("metapivot.tool.args_summary", args_summary)
            yield span
        except Exception as e:
            span.record_exception(e)
            raise


@contextmanager
def agent_step_span(node_name: str, task_id: str, step: int):
    """Agent 节点 span（intent/planner/executor/reflector/replier）"""
    tracer = _get_tracer()
    if tracer is None:
        yield _NoopSpan()
        return
    with tracer.start_as_current_span(f"agent.{node_name}") as span:
        try:
            span.set_attribute("metapivot.task_id", task_id)
            span.set_attribute("metapivot.step", step)
            span.set_attribute("metapivot.node", node_name)
            span.set_attribute("metapivot.request_id", get_request_id())
            yield span
        except Exception as e:
            span.record_exception(e)
            raise


class _NoopSpan:
    """空 span 实现（OTel 未启用时使用）"""

    def set_attribute(self, key, value):
        pass

    def record_exception(self, e):
        pass

    def add_event(self, name, attributes=None):
        pass


def _get_tracer() -> Any:
    """获取 tracer（OTel 未启用时返回 None，span_helper 直接 yield _NoopSpan）"""
    try:
        from app.infra.observability.otel import NoopTracer, get_tracer
        tracer = get_tracer()
        if isinstance(tracer, NoopTracer):
            return None
        return tracer
    except Exception:
        return None


def _summarize_messages(messages: list) -> str:
    """摘要 messages（PII 脱敏 + 截断前 200 字符）"""
    try:
        from app.domain.agent.guardrail import sanitize_output
        text = json.dumps(messages, ensure_ascii=False, default=str)[:200]
        return sanitize_output(text)
    except Exception:
        return ""


def _summarize_args(args: dict) -> str:
    """摘要工具参数（PII 脱敏 + 截断前 500 字符）"""
    try:
        from app.domain.agent.guardrail import sanitize_output
        text = json.dumps(args, ensure_ascii=False, default=str)[:500]
        return sanitize_output(text)
    except Exception:
        return ""
