"""请求上下文管理 - 基于 contextvars 实现 request_id/trace_id 跨 asyncio.create_task 传播

解决痛点：
- loguru 的 bind() 不会自动透传到 asyncio.create_task 创建的子 task
- 后台任务（如 AgentService._run_task）的日志无 request_id，无法关联主请求

方案：
- 用 contextvars.ContextVar 存储 request_id/trace_id
- 主请求中间件 set_request_context()
- 启动后台任务前 contextvars.copy_context() 透传
- loguru patcher 从 contextvars 注入到 record["extra"]
"""
import contextvars
from typing import Optional

# 上下文变量（Python 3.7+ 原生支持跨 asyncio task 传播）
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")
ip_address_var: contextvars.ContextVar[str] = contextvars.ContextVar("ip_address", default="")


def set_request_context(
    request_id: str,
    trace_id: str = "",
    user_id: str = "",
    ip_address: str = "",
) -> None:
    """设置请求上下文（在请求中间件中调用）"""
    request_id_var.set(request_id or "")
    trace_id_var.set(trace_id or request_id or "")
    if user_id:
        user_id_var.set(user_id)
    if ip_address:
        ip_address_var.set(ip_address)


def clear_request_context() -> None:
    """清除请求上下文"""
    request_id_var.set("")
    trace_id_var.set("")
    user_id_var.set("")
    ip_address_var.set("")


def get_request_id() -> str:
    """获取当前请求 ID（从 contextvars）"""
    return request_id_var.get()


def get_trace_id() -> str:
    """获取当前追踪 ID"""
    return trace_id_var.get()


def get_user_id() -> str:
    """获取当前用户 ID"""
    return user_id_var.get()


def get_ip_address() -> str:
    """获取当前请求 IP"""
    return ip_address_var.get()


def snapshot() -> dict:
    """获取当前上下文快照（用于日志/审计）"""
    return {
        "request_id": request_id_var.get(),
        "trace_id": trace_id_var.get(),
        "user_id": user_id_var.get(),
        "ip_address": ip_address_var.get(),
    }


def get_context_id() -> str:
    """获取用于日志的上下文标识（request_id 优先，否则 trace_id）"""
    rid = request_id_var.get()
    if rid:
        return rid
    return trace_id_var.get() or "-"
