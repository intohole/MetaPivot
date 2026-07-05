"""ChromaVectorStore - ChromaDB 向量库实现（IVectorStore）

支持部署规模：
- 本地持久化：VECTOR_BACKEND=chroma + CHROMA_PATH=data/chroma（小企业/单机，零外部服务依赖）
- 远程服务：VECTOR_BACKEND=chroma + CHROMA_HOST=localhost + CHROMA_PORT=8001（多实例共享）

设计要点：
- 委托 ILLMProvider.embed 计算向量（由调用方完成），不依赖 chroma 内置 embedding function
- collection 命名空间隔离（knowledge_chunks / agent_memory / tool_index）
- get_or_create_collection 幂等，首次 upsert 时自动建立
- filter_expr：Chroma 用 metadata where 子句（dict），本实现做 best-effort JSON 解析

实现 IVectorStore Protocol（app.domain.contracts.vector.IVectorStore）。
"""
import json
from typing import Any, Optional

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("chroma_vector")


class ChromaVectorStore:
    """ChromaDB 向量库（结构化满足 IVectorStore Protocol）"""

    def __init__(self) -> None:
        # 延迟初始化 client（避免 import 时连接），首次 _client 访问时建立
        self._client: Any = None

    def _get_client(self) -> Any:
        """懒加载 Chroma client（PersistentClient 优先，配置 host 时用 HttpClient）"""
        if self._client is not None:
            return self._client
        import chromadb  # 延迟 import，避免 chromadb 未安装时启动崩溃

        host = getattr(settings, "chroma_host", "") or ""
        port = getattr(settings, "chroma_port", 8001)
        path = getattr(settings, "chroma_path", "data/chroma")

        if host:
            # 远程 Chroma 服务（多实例共享）
            self._client = chromadb.HttpClient(host=host, port=int(port))
            log.info("ChromaVectorStore connected: http://{}:{}", host, port)
        else:
            # 本地持久化（单机，零外部服务依赖）
            self._client = chromadb.PersistentClient(path=path)
            log.info("ChromaVectorStore persisted at: {}", path)
        return self._client

    def _collection(self, name: str) -> Any:
        """获取或创建 collection（幂等）"""
        client = self._get_client()
        # metadata={"hnsw:space": "cosine"} 用余弦距离，与 LocalVectorStore 语义一致
        return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    @staticmethod
    def _parse_filter(filter_expr: Optional[str]) -> Optional[dict]:
        """best-effort 解析 filter_expr（str → Chroma where dict）

        Chroma where 用 dict（如 {"chat_id": "abc"}），本实现尝试 JSON 解析；
        解析失败返回 None（不过滤），与 LocalVectorStore 忽略 filter_expr 行为对齐。
        """
        if not filter_expr:
            return None
        try:
            parsed = json.loads(filter_expr)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    async def upsert(self, collection: str, points: list[dict]) -> int:
        """批量写入/更新向量

        Args:
            collection: 集合名
            points: [{id, vector, metadata, content}]
        """
        if not points:
            return 0
        try:
            col = self._collection(collection)
            ids = [str(p.get("id", "")) for p in points]
            embeddings = [p["vector"] for p in points]
            documents = [p.get("content", "") for p in points]
            metadatas = [p.get("metadata", {}) or {} for p in points]
            col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
            return len(points)
        except Exception as e:
            log.warning("chroma upsert failed collection={} err={}", collection, e)
            return 0

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> list[dict]:
        """向量近邻检索

        Returns:
            [{id, score, content, metadata}]（score 为 1 - distance，与余弦相似度语义一致）
        """
        if not query_vector:
            return []
        try:
            col = self._collection(collection)
            where = self._parse_filter(filter_expr)
            kwargs: dict = {"query_embeddings": [query_vector], "n_results": top_k}
            if where is not None:
                kwargs["where"] = where
            res = col.query(**kwargs)
            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            out: list[dict] = []
            for idx, doc_id in enumerate(ids):
                # Chroma distance 是余弦距离（0=完全相同，2=完全相反）
                # 转换为相似度 score（1 - distance），与 LocalVectorStore 的 _cosine 输出对齐
                distance = dists[idx] if idx < len(dists) else 1.0
                score = max(0.0, 1.0 - float(distance))
                out.append({
                    "id": doc_id,
                    "score": score,
                    "content": docs[idx] if idx < len(docs) else "",
                    "metadata": metas[idx] if idx < len(metas) else {},
                })
            return out
        except Exception as e:
            log.warning("chroma search failed collection={} err={}", collection, e)
            return []

    async def delete(self, collection: str, ids: list[str]) -> int:
        """按 ID 删除向量"""
        if not ids:
            return 0
        try:
            col = self._collection(collection)
            col.delete(ids=[str(i) for i in ids])
            return len(ids)
        except Exception as e:
            log.warning("chroma delete failed collection={} err={}", collection, e)
            return 0

    async def count(self, collection: str) -> int:
        """统计集合中文档数量"""
        try:
            col = self._collection(collection)
            return int(col.count())
        except Exception as e:
            log.warning("chroma count failed collection={} err={}", collection, e)
            return 0

    async def drop_collection(self, collection: str) -> None:
        """删除整个集合（危险操作）"""
        try:
            client = self._get_client()
            client.delete_collection(name=collection)
            log.info("Chroma collection dropped: {}", collection)
        except Exception as e:
            log.warning("chroma drop_collection failed collection={} err={}", collection, e)

    async def close(self) -> None:
        """关闭连接（应用关闭时调用）"""
        # Chroma client 无显式 close（PersistentClient 随进程退出释放）
        self._client = None
        log.info("ChromaVectorStore closed")
