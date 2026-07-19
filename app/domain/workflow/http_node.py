"""http_request 节点执行器 — Workflow 中调用外部 HTTP API

节点 config schema:
    method: str - HTTP 方法 (GET/POST/PUT/DELETE/PATCH)，默认 GET
    url: str - 请求 URL（支持 ${var} 变量替换）
    headers: dict - 请求头（支持 ${var}）
    params: dict - URL query 参数（支持 ${var}）
    body: any - 请求体（dict/list 自动 JSON 序列化；str 原样发送）
    auth: dict - 鉴权 {type: bearer/api_key/basic/none, token/key/username/password, header_name}
    timeout: float - 超时秒数（默认 30）
    retry: int - 重试次数（默认 3，仅 5xx + 网络错误重试）
    allow_private_ip: bool - 是否允许内网 IP（默认 False，SSRF 防护）

输出:
    {status_code, headers, body, body_text, duration_ms}
    body 优先解析为 JSON（失败保留原始字符串）
    存入 context["outputs"]["http_<node_id>"] 供下游 ${var} 引用
"""
from app.utils.logger import get_logger

log = get_logger("workflow_http_node")


async def exec_http_request(config: dict, context: dict, node_id: str = "") -> dict:
    """执行 http_request 节点（Sprint 9.1: 外部 API 接入）

    Args:
        config: 节点配置（含 method/url/headers/params/body/auth/timeout/retry）
        context: 工作流执行上下文（含 variables 用于 ${var} 替换）
        node_id: 节点 ID（用于 outputs key 命名，便于下游引用）

    Returns:
        HTTP 响应 {status_code, headers, body, body_text, duration_ms} 或 {error, status_code: 0}
    """
    from app.infra.tools.http_client import http_client
    from app.domain.workflow.variables import resolve_vars, resolve_vars_str

    # 变量替换（url/method/headers/params/body/auth 均支持 ${var}）
    method = resolve_vars_str(config.get("method", "GET"), context)
    url = resolve_vars_str(config.get("url", ""), context)
    if not url:
        result = {"error": "url 未配置", "status_code": 0}
        output_key = f"http_{node_id}" if node_id else "http"
        context.setdefault("outputs", {})[output_key] = result
        return result

    headers = resolve_vars(config.get("headers", {}), context)
    params = resolve_vars(config.get("params", {}), context)
    raw_body = config.get("body")
    body = resolve_vars(raw_body, context) if raw_body is not None else None
    auth = resolve_vars(config.get("auth", {}), context)
    timeout = config.get("timeout", 30)
    retry = config.get("retry", 3)
    allow_private_ip = bool(config.get("allow_private_ip", False))

    try:
        result = await http_client.request(
            method=method,
            url=url,
            headers=headers if isinstance(headers, dict) else None,
            params=params if isinstance(params, dict) else None,
            body=body,
            auth=auth if isinstance(auth, dict) else None,
            timeout=float(timeout) if timeout else None,
            retry=int(retry) if retry is not None else None,
            allow_private_ip=allow_private_ip,
        )
    except Exception as e:
        log.warning("http_request node {} failed: {}", node_id or url, e)
        result = {"error": str(e), "status_code": 0}

    # 存入 outputs 供下游节点 ${var} 引用（成功 + 失败均写入，便于 condition 判断）
    output_key = f"http_{node_id}" if node_id else "http"
    context.setdefault("outputs", {})[output_key] = result
    if result.get("status_code", 0) > 0:
        log.info("http_request {} {} -> {} ({}ms)", method, url,
                 result.get("status_code"), result.get("duration_ms"))
    return result