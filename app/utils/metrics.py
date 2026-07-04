"""Prometheus 指标定义 - 链路可见性与系统健康度

指标分组：
1. HTTP：请求计数/延迟（middleware 自动采集）
2. Agent：任务计数/延迟/活跃数/Token 用量
3. LLM：调用计数/延迟（executor_node 采集）
4. Skill：调用计数（skill_service 采集）
5. Workflow：执行计数（workflow_service 采集）

通过 /metrics 端点暴露（prometheus_client generate_latest）。
所有指标非阻塞（prometheus_client 内部线程安全），失败不影响主流程。
"""
from typing import Optional

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

from app.utils.logger import get_logger

log = get_logger("metrics")

# 自定义 registry（避免全局 registry 污染，便于测试隔离）
_REGISTRY = CollectorRegistry() if _HAS_PROMETHEUS else None


def _counter(name, desc, labels=()):
    if not _HAS_PROMETHEUS:
        return None
    return Counter(name, desc, list(labels), registry=_REGISTRY)


def _histogram(name, desc, labels=(), buckets=None):
    if not _HAS_PROMETHEUS:
        return None
    return Histogram(name, desc, list(labels), registry=_REGISTRY, buckets=buckets)


def _gauge(name, desc, labels=()):
    if not _HAS_PROMETHEUS:
        return None
    return Gauge(name, desc, list(labels), registry=_REGISTRY)


# ============ HTTP ============
HTTP_REQUESTS = _counter(
    "metapivot_http_requests_total", "HTTP 请求总数",
    labels=("method", "path", "status"),
)
HTTP_REQUEST_DURATION = _histogram(
    "metapivot_http_request_duration_seconds", "HTTP 请求延迟（秒）",
    labels=("method", "path"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# ============ Agent ============
AGENT_TASKS = _counter(
    "metapivot_agent_tasks_total", "Agent 任务总数",
    labels=("status",),
)
AGENT_TASK_DURATION = _histogram(
    "metapivot_agent_task_duration_seconds", "Agent 任务耗时（秒）",
    labels=(),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)
AGENT_ACTIVE_TASKS = _gauge(
    "metapivot_agent_active_tasks", "当前活跃 Agent 任务数",
)
AGENT_TOKEN_USAGE = _counter(
    "metapivot_agent_token_usage_total", "Agent Token 用量",
    labels=("type",),  # type: prompt / completion / total
)

# ============ LLM ============
LLM_CALLS = _counter(
    "metapivot_llm_calls_total", "LLM 调用总数",
    labels=("model", "status"),
)
LLM_CALL_DURATION = _histogram(
    "metapivot_llm_call_duration_seconds", "LLM 调用延迟（秒）",
    labels=("model",),
    buckets=(0.5, 1, 2, 5, 10, 30),
)

# ============ Skill ============
SKILL_CALLS = _counter(
    "metapivot_skill_calls_total", "Skill 调用总数",
    labels=("skill_name", "status"),
)

# ============ Workflow ============
WORKFLOW_EXECUTIONS = _counter(
    "metapivot_workflow_executions_total", "工作流执行总数",
    labels=("status",),
)


def record_http_request(method: str, path: str, status: int, duration: float) -> None:
    """记录 HTTP 请求指标（非阻塞）"""
    if not _HAS_PROMETHEUS:
        return
    try:
        HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
        HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)
    except Exception as e:
        log.warning("record_http_request failed: {}", e)


def record_agent_task(status: str, duration: Optional[float] = None) -> None:
    """记录 Agent 任务指标（非阻塞）"""
    if not _HAS_PROMETHEUS:
        return
    try:
        AGENT_TASKS.labels(status=status).inc()
        if duration is not None:
            AGENT_TASK_DURATION.observe(duration)
    except Exception as e:
        log.warning("record_agent_task failed: {}", e)


def agent_task_started() -> None:
    """Agent 任务启动（活跃数 +1）"""
    if _HAS_PROMETHEUS:
        try:
            AGENT_ACTIVE_TASKS.inc()
        except Exception:
            pass


def agent_task_finished() -> None:
    """Agent 任务结束（活跃数 -1）"""
    if _HAS_PROMETHEUS:
        try:
            AGENT_ACTIVE_TASKS.dec()
        except Exception:
            pass


def record_token_usage(usage: dict) -> None:
    """记录 LLM Token 用量（非阻塞）"""
    if not _HAS_PROMETHEUS or not usage:
        return
    try:
        prompt = int(usage.get("prompt_tokens", 0))
        completion = int(usage.get("completion_tokens", 0))
        total = int(usage.get("total_tokens", 0))
        if prompt:
            AGENT_TOKEN_USAGE.labels(type="prompt").inc(prompt)
        if completion:
            AGENT_TOKEN_USAGE.labels(type="completion").inc(completion)
        if total:
            AGENT_TOKEN_USAGE.labels(type="total").inc(total)
    except Exception as e:
        log.warning("record_token_usage failed: {}", e)


def record_llm_call(model: str, status: str, duration: Optional[float] = None) -> None:
    """记录 LLM 调用指标（非阻塞）"""
    if not _HAS_PROMETHEUS:
        return
    try:
        LLM_CALLS.labels(model=model, status=status).inc()
        if duration is not None:
            LLM_CALL_DURATION.labels(model=model).observe(duration)
    except Exception as e:
        log.warning("record_llm_call failed: {}", e)


def record_skill_call(skill_name: str, status: str) -> None:
    """记录 Skill 调用（非阻塞）"""
    if not _HAS_PROMETHEUS:
        return
    try:
        SKILL_CALLS.labels(skill_name=skill_name, status=status).inc()
    except Exception as e:
        log.warning("record_skill_call failed: {}", e)


def record_workflow_execution(status: str) -> None:
    """记录工作流执行（非阻塞）"""
    if not _HAS_PROMETHEUS:
        return
    try:
        WORKFLOW_EXECUTIONS.labels(status=status).inc()
    except Exception as e:
        log.warning("record_workflow_execution failed: {}", e)


def render_metrics() -> bytes:
    """渲染 Prometheus 文本格式指标（供 /metrics 端点使用）"""
    if not _HAS_PROMETHEUS:
        return b"# prometheus_client not installed\n"
    return generate_latest(_REGISTRY)


def has_prometheus() -> bool:
    """是否安装了 prometheus_client"""
    return _HAS_PROMETHEUS
