"""RAG 知识库检索 - 占位实现

P5 阶段接入 Milvus 向量库后实现完整逻辑。
当前为关键词匹配的兜底实现，确保 Skill 可调用。
"""
from app.utils.logger import get_logger

log = get_logger("rag")


async def search(args: dict) -> dict:
    """知识库检索

    args:
        query: 检索关键词
        top_k: 返回数量（默认5）
    """
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    log.info("RAG search (keyword fallback): query='{}' top_k={}", query, top_k)

    # 兜底：返回空结果（待 P5 接入 Milvus）
    return {
        "query": query,
        "results": [],
        "total": 0,
        "engine": "keyword_fallback",
        "note": "向量检索未启用，请联系管理员配置 Milvus",
    }
