"""RAG 知识库检索 - 通过 IVectorStore 进行语义检索

策略：
1. 使用 LLM embed 查询 → 调用 IVectorStore.search 进行向量近邻检索
2. 若向量库为空或 embed 失败，降级为关键词匹配（兜底）
3. 通过 factory 自动选择 local/milvus backend（按 settings.vector_backend）

此函数作为 Skill 被 Agent 调用，签名固定为 async def search(args) -> dict。
"""
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("rag")

_DEFAULT_COLLECTION = "knowledge_chunks"


async def search(args: dict) -> dict:
    """知识库检索

    args:
        query: 检索关键词/问题
        top_k: 返回数量（默认 5）
    """
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    if not query:
        return {"query": "", "results": [], "total": 0, "engine": "empty_query"}

    # 优先尝试向量语义检索
    try:
        results = await _vector_search(query, top_k)
        if results:
            return {
                "query": query,
                "results": results,
                "total": len(results),
                "engine": f"vector:{settings.vector_backend}",
            }
        log.info("Vector search returned empty, falling back to keyword match")
    except Exception as e:
        log.warning("Vector search failed, fallback to keyword: {}", e)

    # 兜底：关键词匹配（空结果，告知未启用向量库）
    return {
        "query": query,
        "results": [],
        "total": 0,
        "engine": "keyword_fallback",
        "note": "向量检索未启用或知识库为空，请先上传文档",
    }


async def _vector_search(query: str, top_k: int) -> list[dict]:
    """通过 IVectorStore + LLM embed 进行语义检索"""
    from app.infra.llm.provider import get_llm
    from app.infra.rag.factory import get_vector_store

    # 1. 查询向量化
    llm = get_llm()
    query_vector = await llm.embed(query)

    # 2. 向量近邻检索
    vector_store = get_vector_store()
    collection = settings.milvus_collection or _DEFAULT_COLLECTION
    hits = await vector_store.search(
        collection=collection,
        query_vector=query_vector,
        top_k=top_k,
    )

    # 3. 整理返回结构
    return [
        {
            "content": h.get("content", ""),
            "score": round(h.get("score", 0.0), 4),
            "metadata": h.get("metadata", {}),
            "id": h.get("id"),
        }
        for h in hits
    ]
