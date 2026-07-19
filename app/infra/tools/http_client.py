"""HTTP 客户端 — Workflow http_request 节点 + Skill source_type=http 共用基础设施

特性：
- httpx.AsyncClient 单例（连接池复用，避免每次握手开销）
- SSRF 防护（默认拦截内网 IP：127.0.0.0/8 / 10.0.0.0/8 / 172.16.0.0/12 /
  192.168.0.0/16 / 169.254.0.0/16 / ::1 / fc00::/7 / fe80::/10）
- 自动重试（3 次指数退避 0.5s/1s/2s，仅 5xx + 网络错误重试）
- 超时控制（connect 10s + read 30s，可配置）
- 三种鉴权：Bearer Token / API Key / Basic Auth

使用：
    from app.infra.tools.http_client import http_client
    result = await http_client.request(
        method="POST", url="https://api.example.com/v1/items",
        headers={"X-Request-Id": "abc"},
        body={"name": "foo"},
        auth={"type": "bearer", "token": "..."},
    )
    # result = {status_code, headers, body, body_text, duration_ms}

SSRF 防护：
- 默认拒绝所有内网 IP（含 DNS 解析后的所有 A 记录）
- 测试/内网部署可设置 allow_private_ip=True 放行（节点级配置）
"""
import asyncio
import base64
import ipaddress
import socket
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("http_client")

# SSRF 防护：默认拦截的内网网段
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # IPv4 loopback
    ipaddress.ip_network("10.0.0.0/8"),        # IPv4 private A
    ipaddress.ip_network("172.16.0.0/12"),     # IPv4 private B
    ipaddress.ip_network("192.168.0.0/16"),    # IPv4 private C
    ipaddress.ip_network("169.254.0.0/16"),    # IPv4 link-local
    ipaddress.ip_network("0.0.0.0/8"),         # IPv4 unspecified
    ipaddress.ip_network("100.64.0.0/10"),     # IPv4 carrier-grade NAT
    ipaddress.ip_network("198.18.0.0/15"),     # IPv4 benchmark testing
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.5  # 0.5s, 1s, 2s
_DEFAULT_TIMEOUT = 30.0
_CONNECT_TIMEOUT = 10.0


class HttpClient:
    """HTTP 客户端单例（httpx.AsyncClient 复用连接池）"""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_DEFAULT_TIMEOUT, write=30.0, pool=5.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                # 安全默认：禁用重定向，防止 SSRF 校验被 302→内网 绕过
                # 需要重定向的场景应由用户配置最终 URL
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        """关闭连接池（lifespan shutdown 调用）"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        log.info("HTTP client closed")

    def _check_ssrf(self, url: str, allow_private: bool = False) -> None:
        """SSRF 防护：校验 URL 不指向内网（DNS 解析后所有 IP 都校验）"""
        if allow_private:
            return
        parsed = urlparse(url)
        if not parsed.hostname:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"URL 缺少 hostname: {url}", 400)
        if parsed.scheme not in ("http", "https"):
            raise AppError(ErrorCode.VALIDATION_ERROR, f"仅允许 http/https 协议: {parsed.scheme}", 400)
        hostname = parsed.hostname
        try:
            infos = socket.getaddrinfo(hostname, None)
            ips = {info[4][0] for info in infos}
        except socket.gaierror:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"无法解析 hostname: {hostname}", 400)
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            for net in _PRIVATE_NETWORKS:
                if ip in net:
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR,
                        f"SSRF 防护：禁止访问内网地址 {ip_str}（如需放行请配置 allow_private_ip=true）",
                        400,
                    )

    def _build_auth_header(self, auth: dict) -> dict:
        """构建鉴权头（bearer/api_key/basic 三种类型）"""
        auth_type = auth.get("type", "none")
        if auth_type == "bearer":
            token = auth.get("token", "")
            return {"Authorization": f"Bearer {token}"} if token else {}
        if auth_type == "api_key":
            key = auth.get("key", "")
            header_name = auth.get("header_name", "X-API-Key")
            return {header_name: key} if key else {}
        if auth_type == "basic":
            user = auth.get("username", "")
            pwd = auth.get("password", "")
            if not user:
                return {}
            cred = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            return {"Authorization": f"Basic {cred}"}
        return {}

    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        body: Any = None,
        auth: Optional[dict] = None,
        timeout: Optional[float] = None,
        retry: Optional[int] = None,
        allow_private_ip: bool = False,
    ) -> dict:
        """发起 HTTP 请求（含 SSRF 校验 + 重试）

        Returns:
            {status_code, headers, body, body_text, duration_ms}
            body 优先解析为 JSON（失败保留原始字符串）
        """
        self._check_ssrf(url, allow_private=allow_private_ip)

        all_headers = {"User-Agent": "MetaPivot-Workflow/1.0"}
        if auth:
            all_headers.update(self._build_auth_header(auth))
        if headers:
            all_headers.update(headers)

        json_body = None
        content_body = None
        if body is not None:
            if isinstance(body, (dict, list)):
                json_body = body
                all_headers.setdefault("Content-Type", "application/json")
            elif isinstance(body, str):
                content_body = body.encode("utf-8")
            else:
                content_body = str(body).encode("utf-8")

        max_retries = retry if retry is not None else _MAX_RETRIES
        read_timeout = timeout if timeout else _DEFAULT_TIMEOUT
        req_timeout = httpx.Timeout(_CONNECT_TIMEOUT, read=read_timeout, write=30.0, pool=5.0)

        client = await self._ensure_client()
        start = time.monotonic()
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.request(
                    method=method.upper(), url=url, headers=all_headers, params=params,
                    json=json_body, content=content_body, timeout=req_timeout,
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                # 5xx 重试
                if response.status_code >= 500 and attempt < max_retries:
                    log.warning("HTTP {} {} returned {}, retry {}/{}",
                                method, url, response.status_code, attempt, max_retries)
                    await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                return self._parse_response(response, duration_ms)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                    httpx.PoolTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                if attempt < max_retries:
                    log.warning("HTTP {} {} network error: {}, retry {}/{}",
                                method, url, type(e).__name__, attempt, max_retries)
                    await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                raise AppError(ErrorCode.HTTP_REQUEST_FAILED,
                               f"HTTP 请求失败（重试 {max_retries} 次后仍失败）: {e}", 502)
            except httpx.HTTPError as e:
                raise AppError(ErrorCode.HTTP_REQUEST_FAILED, f"HTTP 请求错误: {e}", 502)

        raise AppError(ErrorCode.HTTP_REQUEST_FAILED, f"HTTP 请求失败: {last_error}", 502)

    def _parse_response(self, response: httpx.Response, duration_ms: int) -> dict:
        """解析响应（body 优先 JSON，否则原始文本）"""
        body_text = response.text
        body: Any = body_text
        try:
            body = response.json()
        except Exception:
            pass  # 保留原始文本
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "body_text": body_text,
            "duration_ms": duration_ms,
        }


# 全局单例
http_client = HttpClient()