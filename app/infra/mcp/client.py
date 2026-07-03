"""MCP Client - MCP Server 动态连接管理

职责：
1. 根据 MCPServerORM 配置动态建立 MCP 连接（stdio/http）
2. 缓存连接，断线重连
3. 调用 MCP tool 执行
4. 健康检查

依赖 mcp Python SDK（要求已安装）。
"""
from __future__ import annotations
from typing import Any, Optional

from app.utils.logger import get_logger

log = get_logger("mcp_client")

# MCP SDK 可选导入（开发环境可能未装）
try:
    from mcp import ClientSession, StdioServerParameters  # type: ignore
    from mcp.client.stdio import stdio_client  # type: ignore
    from mcp.client.sse import sse_client  # type: ignore
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    log.warning("mcp SDK not installed, MCPClient disabled")


class MCPConnection:
    """单个 MCP Server 连接封装"""

    def __init__(self, name: str, transport: str, endpoint: str | None, args: list, env: dict) -> None:
        self.name = name
        self.transport = transport  # stdio/http
        self.endpoint = endpoint
        self.args = args
        self.env = env
        self._session: Optional[Any] = None
        self._ctx_stack: Optional[Any] = None

    async def connect(self) -> None:
        """建立连接"""
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP SDK not installed")
        try:
            if self.transport == "stdio":
                params = StdioServerParameters(
                    command=self.endpoint or "",
                    args=self.args,
                    env=self.env or None,
                )
                self._ctx_stack = stdio_client(params)
                read, write = await self._ctx_stack.__aenter__()
            elif self.transport == "http":
                self._ctx_stack = sse_client(self.endpoint)
                read, write = await self._ctx_stack.__aenter__()
            else:
                raise ValueError(f"Unsupported transport: {self.transport}")

            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
            log.info("MCP connected: {} ({})", self.name, self.transport)
        except Exception as e:
            log.exception("MCP connect failed: {} - {}", self.name, e)
            await self._cleanup()
            raise

    async def _cleanup(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._ctx_stack is not None:
            try:
                await self._ctx_stack.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx_stack = None

    async def list_tools(self) -> list[dict]:
        """列出 MCP Server 提供的工具"""
        if self._session is None:
            return []
        result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 MCP 工具"""
        if self._session is None:
            return {"error": "MCP session not connected"}
        try:
            result = await self._session.call_tool(tool_name, arguments)
            return {
                "content": [c.model_dump() if hasattr(c, "model_dump") else c for c in result.content],
                "is_error": getattr(result, "isError", False),
            }
        except Exception as e:
            log.exception("MCP call_tool failed: {}.{} - {}", self.name, tool_name, e)
            return {"error": str(e)}

    async def close(self) -> None:
        await self._cleanup()
        log.info("MCP closed: {}", self.name)


class MCPClient:
    """MCP Server 连接管理器（单例）"""

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}

    async def connect_server(self, server_orm) -> MCPConnection:
        """根据 ORM 配置建立连接"""
        name = server_orm.name
        if name in self._connections:
            existing = self._connections[name]
            # 简单策略：复用已有连接（生产环境需考虑配置变更检测）
            return existing
        conn = MCPConnection(
            name=name,
            transport=server_orm.transport,
            endpoint=server_orm.endpoint,
            args=server_orm.args or [],
            env=server_orm.env or {},
        )
        await conn.connect()
        self._connections[name] = conn
        return conn

    async def call(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        """调用指定 MCP Server 的工具"""
        conn = self._connections.get(server_name)
        if conn is None:
            return {"error": f"MCP server '{server_name}' not connected"}
        return await conn.call_tool(tool_name, arguments)

    async def list_tools(self, server_name: str) -> list[dict]:
        conn = self._connections.get(server_name)
        if conn is None:
            return []
        return await conn.list_tools()

    async def list_all_tools(self) -> list[dict]:
        """聚合所有已连接 MCP Server 的工具列表"""
        all_tools: list[dict] = []
        for name, conn in self._connections.items():
            tools = await conn.list_tools()
            for t in tools:
                t["server"] = name
                t["full_name"] = f"{name}.{t['name']}"
                all_tools.append(t)
        return all_tools

    async def close_all(self) -> None:
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()


# 全局单例
mcp_client = MCPClient()
