"""UnifiedRetriever - 三库统一检索器

将 knowledge / memory / tool 三库分散检索统一为单一接口。
agent 通过 QueryRouter 决定意图后调 retrieve()，或 fallback 用 retrieve_all() 并行检索。

三库映射：
- knowledge：IVectorStore.search(knowledge_chunks collection) — 复用 rag/search 逻辑
- memory：IMemoryStore.search_semantic() — 跨会话语义召回
- tool：tool_provider 回调（skill_service.list_tools_for_llm）+ 关键词匹配

依赖通过构造函数 DI 注入，不直接 import infra 层（保持分层依赖方向）。
"""
import asyncio
from typing import Awaitable, Callable, Optional

from app.domain.contracts.llm import ILLMProvider
from app.domain.contracts.memory import IMemoryStore
from app.domain.contracts.vector import IVectorStore
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("unified_retrieval")

# tool_provider 回调签名：(permission) -> list[{name, description, parameters, ...}]
ToolProvider = Callable[[str], Awaitable[list[dict]]]


class UnifiedRetriever:
    """三库统一检索器 — knowledge/memory/tool 统一接口"""

    def __init__(
        self,
        vector_store: IVectorStore,
        memory_store: IMemoryStore,
        llm: ILLMProvider,
        tool_provider: ToolProvider,
        knowledge_collection: str = "",
    ) -> None:
        self._vector = vector_store
        self._memory = memory_store
        self._llm = llm
        self._tool_provider = tool_provider
        self._knowledge_collection = knowledge_collection or getattr(
            settings, "milvus_collection", None
        ) or "knowledge_chunks"

    async def retrieve(
        self,
        query: str,
        intent: str,
        top_k: int = 5,
        context: Optional[dict] = None,
    ) -> dict:
        """按意图检索指定库

        intent: direct（不检索）/ knowledge / memory / tool
        """
        if not query or intent == "direct":
            return {"items": [], "source": "none", "total": 0}

        ctx = context or {}
        try:
            if intent == "knowledge":
                return await self._retrieve_knowledge(query, top_k)
            if intent == "memory":
                return await self._retrieve_memory(query, top_k, ctx)
            if intent == "tool":
                return await self._retrieve_tool(query, top_k, ctx)
            log.warning("Unknown intent: {}, skip retrieval", intent)
            return {"items": [], "source": "none", "total": 0}
        except Exception as e:
            log.warning("retrieve failed intent={} query='{}' err={}", intent, query[:30], e)
            return {"items": [], "source": "error", "total": 0, "error": str(e)}

    async def retrieve_all(
        self,
        query: str,
        top_k: int = 5,
        context: Optional[dict] = None,
    ) -> dict:
        """并行检索三库（fallback 模式，意图判断失败时用）"""
        if not query:
            return {"items": [], "source": "all", "total": 0}
        ctx = context or {}
        results = await asyncio.gather(
            self._retrieve_knowledge(query, top_k),
            self._retrieve_memory(query, top_k, ctx),
            self._retrieve_tool(query, top_k, ctx),
            return_exceptions=True,
        )
        items: list[dict] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log.warning("retrieve_all sub-retrieval {} failed: {}", i, r)
                continue
            items.extend(r.get("items", []))
        # 按分数降序
        items.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return {"items": items[: top_k * 3], "source": "all", "total": len(items)}

    # ============ 单库检索 ============

    async def _retrieve_knowledge(self, query: str, top_k: int) -> dict:
        """知识库向量检索（embed query → IVectorStore.search）"""
        try:
            query_vector = await self._llm.embed(query)
        except Exception as e:
            log.warning("knowledge embed failed: {}", e)
            return {"items": [], "source": "knowledge", "total": 0}
        hits = await self._vector.search(
            collection=self._knowledge_collection,
            query_vector=query_vector,
            top_k=top_k,
        )
        items = [
            {
                "content": h.get("content", ""),
                "source_type": "knowledge",
                "score": round(h.get("score", 0.0), 4),
                "metadata": h.get("metadata", {}),
            }
            for h in hits
        ]
        return {"items": items, "source": "knowledge", "total": len(items)}

    async def _retrieve_memory(self, query: str, top_k: int, ctx: dict) -> dict:
        """跨会话语义记忆召回（IMemoryStore.search_semantic）"""
        chat_id = ctx.get("chat_id")
        hits = await self._memory.search_semantic(query, chat_id, top_k)
        items = [
            {
                "content": h.get("content", ""),
                "source_type": "memory",
                "score": round(h.get("score", 0.0), 4),
                "metadata": {
                    "role": h.get("role", ""),
                    **(h.get("metadata") or {}),
                },
            }
            for h in hits
        ]
        return {"items": items, "source": "memory", "total": len(items)}

    async def _retrieve_tool(self, query: str, top_k: int, ctx: dict) -> dict:
        """工具检索（tool_provider + 关键词匹配 tool name/description）"""
        permission = ctx.get("permission", "user")
        tools = await self._tool_provider(permission)
        if not tools:
            return {"items": [], "source": "tool", "total": 0}
        # 简单关键词匹配：query 分词后命中 name/description 的 tool
        query_lower = query.lower()
        query_tokens = {t for t in query_lower.split() if len(t) > 1}
        scored: list[tuple[float, dict]] = []
        for t in tools:
            fn = t.get("function", t)
            name = (fn.get("name") or "").lower()
            desc = (fn.get("description") or "").lower()
            score = 0.0
            # 完整 query 子串匹配
            if query_lower and query_lower in name:
                score += 0.8
            if query_lower and query_lower in desc:
                score += 0.4
            # token 匹配
            for tok in query_tokens:
                if tok in name:
                    score += 0.3
                if tok in desc:
                    score += 0.15
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [
            {
                "content": (t.get("function", t).get("description") or "")[:200],
                "source_type": "tool",
                "score": round(s, 4),
                "metadata": {
                    "name": t.get("function", t).get("name"),
                    "parameters": t.get("function", t).get("parameters", {}),
                    "skill_id": (t.get("metadata") or {}).get("skill_id"),
                },
            }
            for s, t in scored[:top_k]
        ]
        return {"items": items, "source": "tool", "total": len(items)}
