"""Agent RAG 上下文构建 + 记忆管理辅助函数

Sprint 7.4: 从 agent_service.py 拆离，保持 agent_service.py ≤ 300 行。
职责：
- build_rag_context: Adaptive RAG 三库统一检索（向后兼容旧 semantic 逻辑）
- search_memory: 语义记忆检索（对外暴露的查询 API）
- persist_to_memory: 持久化本轮对话到记忆存储（含事实抽取触发）
- maybe_consolidate: 按消息间隔触发事实抽取（fire-and-forget）

依赖方向：service → domain/contracts（IMemoryStore/IRetriever/IQueryRouter）
"""
import asyncio
from typing import Optional

from app.domain.contracts.memory import IMemoryStore
from app.domain.contracts.retrieval import IQueryRouter, IRetriever
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("agent_rag")


def format_semantic_context(hits: list[dict]) -> str:
    """将语义召回结果格式化为 system 消息（注入对话历史前置）"""
    lines = ["【相关记忆（语义召回，仅供参考）】"]
    for h in hits[:5]:
        role = h.get("role", "")
        content = h.get("content", "")
        score = h.get("score", 0.0)
        meta = h.get("metadata", {}) or {}
        # 事实型记忆优先展示（type=fact 是 consolidate 抽取的长期记忆）
        tag = "[事实]" if meta.get("type") == "fact" else f"[{role}]" if role else ""
        lines.append(f"- {tag} {content}（相似度 {score:.2f}）")
    return "\n".join(lines)


def format_unified_context(items: list[dict], intent: str) -> str:
    """将三库统一检索结果格式化为 system 消息"""
    source_label = {"knowledge": "知识库", "memory": "记忆", "tool": "可用工具"}.get(intent, intent)
    lines = [f"【相关{source_label}（Adaptive RAG 检索，仅供参考）】"]
    for it in items[:5]:
        content = (it.get("content") or "")[:200]
        score = it.get("score", 0.0)
        src = it.get("source_type", "")
        meta = it.get("metadata") or {}
        if src == "tool":
            name = meta.get("name", "")
            lines.append(f"- [工具] {name}: {content}（相关度 {score:.2f}）")
        elif src == "memory":
            tag = "[事实]" if meta.get("type") == "fact" else f"[{meta.get('role', '')}]"
            lines.append(f"- {tag} {content}（相似度 {score:.2f}）")
        else:
            lines.append(f"- {content}（相似度 {score:.2f}）")
    return "\n".join(lines)


async def build_rag_context(
    message: str,
    chat_id: str,
    user_id: str,
    memory_store: Optional[IMemoryStore],
    retriever: Optional[IRetriever],
    query_router: Optional[IQueryRouter],
) -> str:
    """Phase 4.1: 构建 RAG 上下文（Adaptive RAG 优先，向后兼容旧 semantic 逻辑）

    Returns:
        格式化的 system 消息文本；无检索结果时返回空字符串
    """
    if not message:
        return ""
    # Adaptive RAG：注入 retriever + query_router 时走三库统一检索
    if retriever is not None and query_router is not None:
        try:
            intent = await query_router.route(message, {"chat_id": chat_id, "user_id": user_id})
            if intent == "direct":
                return ""  # 闲聊/常识不检索（降本）
            result = await retriever.retrieve(
                message, intent, top_k=5,
                context={"chat_id": chat_id, "user_id": user_id, "permission": "user"},
            )
            items = result.get("items", [])
            if not items:
                return ""
            return format_unified_context(items, intent)
        except Exception as e:
            log.warning("Adaptive RAG failed, fallback to semantic: {}", e)
    # 向后兼容：未注入 retriever 时走旧 memory_store.search_semantic
    if settings.memory_backend == "semantic" and memory_store:
        try:
            hits = await memory_store.search_semantic(message, chat_id, top_k=5)
            return format_semantic_context(hits) if hits else ""
        except Exception as e:
            log.warning("semantic search fallback failed: {}", e)
    return ""


async def search_memory(
    memory_store: Optional[IMemoryStore],
    query: str,
    chat_id: str = "",
    top_k: int = 5,
) -> list[dict]:
    """语义记忆检索（memory_backend=semantic 时返回向量召回结果）

    非 semantic 后端返回空列表（无语义检索能力）。
    """
    if not memory_store or not query:
        return []
    try:
        cid = chat_id or None
        hits = await memory_store.search_semantic(query, cid, top_k)
        # 过滤敏感字段，只返回展示所需
        return [
            {
                "role": h.get("role", ""),
                "content": h.get("content", ""),
                "score": round(h.get("score", 0.0), 3),
                "type": (h.get("metadata", {}) or {}).get("type", "message"),
            }
            for h in hits
        ]
    except Exception as e:
        log.warning("search_memory failed query='{}' err={}", query[:30], e)
        return []


async def maybe_consolidate(memory_store: IMemoryStore, chat_id: str) -> None:
    """按消息间隔触发事实抽取（fire-and-forget，不阻塞主链路）

    每条消息追加后检查：消息数达 memory_consolidate_interval 整数倍时触发 consolidate_memories。
    consolidate_memories 内部幂等（fact hash 去重），偶发重复触发无副作用。
    """
    try:
        count = await memory_store.count_history(chat_id)
        interval = settings.memory_consolidate_interval
        if count > 0 and count % interval == 0:
            await memory_store.consolidate_memories(chat_id)
    except Exception as e:
        log.warning("maybe_consolidate failed for {}: {}", chat_id, e)


async def persist_to_memory(
    memory_store: Optional[IMemoryStore],
    chat_id: str,
    user_message: str,
    assistant_answer: str,
) -> None:
    """持久化本轮对话到记忆存储（user + assistant 消息）

    memory_backend=semantic 时用 append_with_embedding（双写 DB + 向量），
    非 semantic 后端 append_with_embedding 降级为 append_message（no-op fallback）。
    """
    if not chat_id or not memory_store or not user_message:
        return
    try:
        await memory_store.append_with_embedding(chat_id, "user", user_message)
        if assistant_answer:
            await memory_store.append_with_embedding(
                chat_id, "assistant", assistant_answer
            )
        # 语义记忆事实抽取（fire-and-forget，每 N 条消息触发一次）
        if (
            settings.memory_backend == "semantic"
            and settings.memory_consolidate_interval > 0
        ):
            asyncio.create_task(maybe_consolidate(memory_store, chat_id))
    except Exception as e:
        log.warning("persist_to_memory failed for {}: {}", chat_id, e)
