"""Agentic RAG 三库合一检索 domain 模块

Phase 4.1：UnifiedRetriever + QueryRouter
- UnifiedRetriever：统一 knowledge/memory/tool 三库检索接口
- QueryRouter：Adaptive RAG 意图分类路由

依赖方向：domain/retrieval → domain/contracts（IVectorStore/IMemoryStore/ILLMProvider）
所有 Infra 依赖通过 DI 注入，不直接 import infra 层。
"""
from app.domain.retrieval.query_router import QueryRouter
from app.domain.retrieval.unified_retrieval import UnifiedRetriever

__all__ = ["UnifiedRetriever", "QueryRouter"]
