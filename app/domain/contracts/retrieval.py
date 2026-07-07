"""IRetriever / IQueryRouter - Agentic RAG 三库统一检索协议

Phase 4.1：将 knowledge / memory / tool 三库分散检索统一为 UnifiedRetriever，
agent 通过 QueryRouter 自主决定检索时机/检索什么/结果是否够用（Adaptive RAG）。

接口约束：
- 所有方法异步
- retrieve() 按意图路由到单库；retrieve_all() 并行检索三库（fallback 模式）
- route() 返回意图字符串（direct/knowledge/memory/tool），驱动 retrieve()
"""
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class IRetriever(Protocol):
    """三库统一检索协议"""

    async def retrieve(
        self,
        query: str,
        intent: str,
        top_k: int = 5,
        context: Optional[dict] = None,
    ) -> dict:
        """按意图检索指定库

        Args:
            query: 用户查询
            intent: 检索意图（direct/knowledge/memory/tool）
            top_k: 返回数量
            context: 上下文（chat_id/user_id/permission 等）

        Returns:
            {"items": [{"content", "source_type", "score", "metadata"}], "source": str}
            direct 意图返回空 items（不检索）
        """
        ...

    async def retrieve_all(
        self,
        query: str,
        top_k: int = 5,
        context: Optional[dict] = None,
    ) -> dict:
        """并行检索三库（fallback 模式，意图判断失败时用）

        Returns:
            {"items": [...], "source": "all", "total": int}
        """
        ...


@runtime_checkable
class IQueryRouter(Protocol):
    """Adaptive RAG 查询路由协议"""

    async def route(
        self,
        query: str,
        context: Optional[dict] = None,
    ) -> str:
        """LLM 意图分类，决定检索策略

        Returns:
            意图字符串：direct（不检索）/ knowledge / memory / tool
        """
        ...


# 检索结果统一项结构（文档型，非强制 Pydantic）
RetrievalItem = dict[str, Any]
"""{"content": str, "source_type": str, "score": float, "metadata": dict}"""
