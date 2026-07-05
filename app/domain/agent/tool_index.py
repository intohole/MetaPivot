"""Tool Index - 工具检索（Tool RAG）

当注册的 Skill 数量较多（>15）时，全量注入 LLM tools= 参数会导致 prompt 膨胀。
ToolIndex 用 query embedding 从工具描述向量库检索 top-k 相关工具子集，
对齐 ToolLLM / Gorilla 的工具检索实践。

设计：
- 延迟 build_index：首次 retrieve 时检查索引是否过期（基于工具列表 hash）
- DI 注入 IVectorStore（复用 RAG 基础设施）
- collection 名 `tool_index`，与 `knowledge_chunks` / `agent_memory` 隔离
- 失败降级为返回原 tools 全量（不阻塞执行）

调用点：nodes.py::intent_node 在 list_tools_for_llm 后判断 should_use_rag
"""
import hashlib
from typing import Any, Optional

from app.utils.logger import get_logger

log = get_logger("tool_index")

_TOOL_COLLECTION = "tool_index"
_RAG_THRESHOLD = 15  # 工具数 > 15 才启用 RAG（小工具集直接全量注入）
_TOP_K = 10


class ToolIndex:
    """工具检索索引（单例，进程内缓存）

    幂等设计：工具列表 hash 未变化则跳过重建索引。
    """

    _instance: Optional["ToolIndex"] = None

    def __init__(self) -> None:
        self._vector_store: Any = None
        self._llm: Any = None
        self._indexed_hash: str = ""  # 已索引的工具列表 hash

    @classmethod
    def get_instance(cls) -> "ToolIndex":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_vector_store(self) -> Any:
        if self._vector_store is None:
            from app.infra.rag.factory import get_vector_store
            self._vector_store = get_vector_store()
        return self._vector_store

    def _get_llm(self) -> Any:
        if self._llm is None:
            from app.infra.llm.provider import get_llm
            self._llm = get_llm()
        return self._llm

    @staticmethod
    def _tools_hash(tools: list[dict]) -> str:
        """计算工具列表的 hash（用于检测是否需要重建索引）"""
        names = sorted(t.get("function", {}).get("name", "") for t in tools)
        raw = "|".join(names)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def should_use_rag(tool_count: int) -> bool:
        """工具数 > 阈值才启用 RAG（小工具集直接全量注入）"""
        return tool_count > _RAG_THRESHOLD

    async def build_index(self, tools: list[dict]) -> None:
        """构建工具索引：把工具 name+description embedding 入向量库

        幂等：工具列表 hash 未变化则跳过
        """
        if not tools:
            return
        tools_hash = self._tools_hash(tools)
        if tools_hash == self._indexed_hash:
            return  # 索引未变化

        vector_store = self._get_vector_store()
        llm = self._get_llm()

        points = []
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            content = f"{name}: {desc}"
            try:
                vec = await llm.embed(content)
                point_id = hashlib.md5(name.encode("utf-8")).hexdigest()[:24]
                points.append({
                    "id": point_id,
                    "vector": vec,
                    "content": content,
                    "metadata": {"name": name, "type": "tool"},
                })
            except Exception as e:
                log.warning("embed tool {} failed: {}", name, e)

        if points:
            try:
                await vector_store.upsert(_TOOL_COLLECTION, points)
                self._indexed_hash = tools_hash
                log.info("ToolIndex built: {} tools", len(points))
            except Exception as e:
                log.warning("ToolIndex build_index failed: {}", e)

    async def retrieve(self, query: str, tools: list[dict], top_k: int = _TOP_K) -> list[dict]:
        """检索与 query 相关的 top-k 工具子集

        Args:
            query: 用户原始消息
            tools: 全量工具列表（OpenAI 格式）
            top_k: 返回前 K 个

        Returns:
            相关工具子集（OpenAI 格式，与输入一致）
            失败时降级返回原 tools 全量
        """
        if not tools or not query:
            return tools

        # 确保索引已构建
        await self.build_index(tools)

        vector_store = self._get_vector_store()
        llm = self._get_llm()

        try:
            query_vec = await llm.embed(query)
            hits = await vector_store.search(_TOOL_COLLECTION, query_vec, top_k=top_k)
            if not hits:
                return tools

            # 按 score 排序，取 top_k 的 name
            hit_names = [h.get("metadata", {}).get("name") for h in hits
                         if h.get("metadata", {}).get("name")]
            name_set = set(hit_names)

            # 保持原 tools 顺序（避免 LLM 看到乱序工具）
            retrieved = [t for t in tools if t.get("function", {}).get("name") in name_set]

            # 若检索结果过少，回退全量（避免 LLM 缺工具）
            if len(retrieved) < min(5, len(tools)):
                log.info("ToolIndex retrieved too few ({}), fallback to all", len(retrieved))
                return tools

            log.info("ToolIndex retrieved {}/{} tools", len(retrieved), len(tools))
            return retrieved
        except Exception as e:
            log.warning("ToolIndex retrieve failed, fallback to all: {}", e)
            return tools


def get_tool_index() -> ToolIndex:
    """工厂方法：获取 ToolIndex 单例"""
    return ToolIndex.get_instance()
