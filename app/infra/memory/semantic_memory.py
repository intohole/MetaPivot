"""SemanticMemoryStore - 语义记忆存储（episodic + semantic + procedural 三层）

在 DBMemoryStore 基础上叠加向量记忆，实现 LangMem 三类记忆模型：
- episodic（事件记忆）：原始对话消息，委托 DBMemoryStore（按 chat_id 顺序存 ChatMessageORM）
- semantic（语义记忆）：消息 embedding + 事实抽取，存 IVectorStore agent_memory collection
- procedural（过程记忆）：复用 episodic 顺序 + semantic 召回（暂不独立存储）

设计要点（参考 Mem0 双阶段 + LangMem 三类记忆）：
- append_with_embedding：存原文 + 计算 embedding 入向量库（双写，单一数据源）
- search_semantic：embed query → 向量检索 → 返回相关事实/消息（跨会话语义召回）
- consolidate_memories：Mem0 风格事实抽取（LLM 从对话抽取三元组，省 90% token）
  - 仅抽取新事实（去重：用 content hash 作为 point_id 幂等）
  - 抽取的事实带 metadata.type="fact"，与原始消息区分
  - 后台 fire-and-forget 调用（不阻塞主链路）

部署场景：MEMORY_BACKEND=semantic（中型企业，需跨会话语义关联）
依赖：VECTOR_BACKEND（chroma/local/milvus）+ LLM embed 能力

实现 IMemoryStore Protocol（含 3 个语义扩展方法）。
"""
import hashlib
import json
from typing import Any, Optional
from uuid import uuid4

from app.infra.memory.db_memory import DBMemoryStore
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("memory_semantic")

# 事实抽取 prompt（Mem0 风格：从对话中抽取持久事实，省 token）
# 注意：模板内 JSON 花括号必须转义为 {{ }} ，避免 str.format() 解析为占位符
_FACT_EXTRACT_PROMPT = """你是一个记忆事实抽取器。从下面的对话中抽取值得长期记忆的事实（用户偏好、习惯、项目背景、关键决策等）。

要求：
1. 只抽取确定的事实，不抽取临时对话内容
2. 每条事实简洁明确（一句话，主谓宾完整）
3. 用 JSON 输出：{{"facts": [{{"content": "事实内容", "type": "preference|background|decision|other"}}]}}
4. 若无值得抽取的事实，返回 {{"facts": []}}

对话内容：
{conversation}
"""


class SemanticMemoryStore(DBMemoryStore):
    """语义记忆存储（episodic + semantic 双层）

    通过 DI 注入 vector_store 和 llm_provider，避免 Domain→Infra 向上依赖。
    委托 DBMemoryStore 存原文（episodic），叠加 IVectorStore 存向量（semantic）。
    """

    def __init__(self, vector_store: Any, llm_provider: Any) -> None:
        super().__init__()
        self._vector_store = vector_store
        self._llm_provider = llm_provider
        self._collection = settings.chroma_memory_collection

    @staticmethod
    def _point_id(chat_id: str, role: str, content: str) -> str:
        """稳定 ID（content hash 幂等，重复内容 upsert 而非新增）"""
        raw = f"{chat_id}:{role}:{content}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _fact_id(chat_id: str, fact_content: str) -> str:
        """事实 ID（chat_id + fact hash，幂等去重）"""
        raw = f"fact:{chat_id}:{fact_content}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:24]

    async def append_with_embedding(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """存原文 + 计算 embedding 入向量库（episodic + semantic 双写）

        episodic：委托 DBMemoryStore.append_message（顺序存 ChatMessageORM）
        semantic：embed content → upsert 到 IVectorStore agent_memory collection
        """
        # 1. episodic 原文（保留 DBMemoryStore 顺序读能力）
        await self.append_message(chat_id, role, content, metadata)

        # 2. semantic 向量（embedding 入向量库，供 search_semantic 召回）
        if not content or not content.strip():
            return
        try:
            vec = await self._llm_provider.embed(content)
            if not vec:
                return
            point_id = self._point_id(chat_id, role, content)
            meta = dict(metadata or {})
            meta.update({"chat_id": chat_id, "role": role, "type": "message"})
            await self._vector_store.upsert(self._collection, [{
                "id": point_id,
                "vector": vec,
                "content": content,
                "metadata": meta,
            }])
        except Exception as e:
            # embedding/向量入库失败不阻塞主链路（episodic 已存，降级为纯 DB 记忆）
            log.warning("append_with_embedding vector failed chat_id={} err={}", chat_id, e)

    async def search_semantic(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[dict]:
        """跨会话语义召回相关记忆（向量近邻检索）

        Returns:
            [{role, content, score, metadata}]（按相似度降序）
            chat_id 限定单会话；None 跨所有会话语义检索（召回全局相关事实）
        """
        if not query or not query.strip():
            return []
        try:
            qvec = await self._llm_provider.embed(query)
            if not qvec:
                return []
            # filter_expr：Chroma 用 JSON dict（{"chat_id":"abc"}），Milvus 用字符串
            # Local 忽略；这里传 JSON，由各 backend best-effort 解析
            filter_expr = json.dumps({"chat_id": chat_id}) if chat_id else None
            results = await self._vector_store.search(
                self._collection, qvec, top_k=top_k, filter_expr=filter_expr,
            )
            out: list[dict] = []
            for r in results:
                meta = r.get("metadata", {}) or {}
                out.append({
                    "role": meta.get("role", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                    "metadata": meta,
                })
            log.info("search_semantic query='{}' chat_id={} hits={}", query[:30], chat_id, len(out))
            return out
        except Exception as e:
            log.warning("search_semantic failed query='{}' err={}", query[:30], e)
            return []

    async def consolidate_memories(self, chat_id: str) -> None:
        """Mem0 风格事实抽取（从最近对话抽取长期记忆）

        双阶段设计（省 90% token）：
        1. LLM 从最近 N 条消息抽取事实三元组
        2. 抽取的事实 embed 后存 IVectorStore（metadata.type="fact"），后续 search_semantic 直接召回

        幂等：用 chat_id + fact_content hash 作 point_id，重复抽取 upsert 而非新增。
        """
        if not chat_id:
            return
        try:
            # 1. 加载最近对话（episodic）
            msgs = await self.load_history(chat_id, limit=20)
            if len(msgs) < 2:
                return
            conversation = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in msgs
            )

            # 2. LLM 抽取事实
            prompt = _FACT_EXTRACT_PROMPT.format(conversation=conversation[:4000])
            result = await self._llm_provider.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=500,
            )
            content_str = result.get("content", "").strip()
            facts = self._parse_facts(content_str)
            if not facts:
                log.info("consolidate_memories no facts extracted chat_id={}", chat_id)
                return

            # 3. 事实 embed + 入库（带 type=fact 区分原始消息）
            for fact in facts:
                fact_content = fact.get("content", "").strip()
                if not fact_content:
                    continue
                vec = await self._llm_provider.embed(fact_content)
                if not vec:
                    continue
                point_id = self._fact_id(chat_id, fact_content)
                meta = {
                    "chat_id": chat_id,
                    "role": "system",
                    "type": "fact",
                    "fact_type": fact.get("type", "other"),
                }
                await self._vector_store.upsert(self._collection, [{
                    "id": point_id,
                    "vector": vec,
                    "content": fact_content,
                    "metadata": meta,
                }])
            log.info("consolidate_memories chat_id={} extracted {} facts", chat_id, len(facts))
        except Exception as e:
            log.warning("consolidate_memories failed chat_id={} err={}", chat_id, e)

    @staticmethod
    def _parse_facts(content: str) -> list[dict]:
        """解析 LLM 事实抽取输出（容错 JSON）"""
        try:
            parsed = json.loads(content)
            facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
            return [f for f in facts if isinstance(f, dict) and f.get("content")]
        except Exception:
            return []
