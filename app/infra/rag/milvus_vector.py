"""MilvusVectorStore - Milvus 分布式向量库实现（IVectorStore）

支持部署规模：
- 超大型企业（百万级 chunk），通过 VECTOR_BACKEND=milvus 启用
- 远程服务：MILVUS_HOST + MILVUS_PORT（需独立部署 Milvus 服务）

设计要点：
- 用 pymilvus.MilvusClient（2.4+ 简化 API）
- collection schema：id(VARCHAR PK) + vector(FLOAT_VECTOR) + content(VARCHAR) + metadata(JSON)
- 首次 upsert 时按向量维度自动建 collection（幂等）
- 距离度量 COSINE，与 LocalVectorStore / ChromaVectorStore 语义对齐
- filter_expr：透传给 Milvus（Milvus 用字符串表达式，如 chat_id == "abc"）

实现 IVectorStore Protocol（app.domain.contracts.vector.IVectorStore）。
"""
from typing import Any, Optional

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("milvus_vector")


class MilvusVectorStore:
    """Milvus 分布式向量库（结构化满足 IVectorStore Protocol）"""

    def __init__(self) -> None:
        self._client: Any = None
        # collection -> vector dimension（首次 upsert 时记录，后续校验）
        self._dims: dict[str, int] = {}

    def _get_client(self) -> Any:
        """懒加载 Milvus client"""
        if self._client is not None:
            return self._client
        from pymilvus import MilvusClient

        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        self._client = MilvusClient(uri=uri)
        log.info("MilvusVectorStore connected: {}", uri)
        return self._client

    def _ensure_collection(self, collection: str, dim: int) -> Any:
        """确保 collection 存在（首次按维度创建）"""
        client = self._get_client()
        from pymilvus import DataType

        if client.has_collection(collection):
            return client
        # 自动建表：id PK + vector + content + metadata
        schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("metadata", DataType.JSON)
        # COSINE 距离，与 LocalVectorStore / ChromaVectorStore 语义一致
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(
            collection_name=collection, schema=schema, index_params=index_params,
        )
        self._dims[collection] = dim
        log.info("Milvus collection created: {} dim={}", collection, dim)
        return client

    async def upsert(self, collection: str, points: list[dict]) -> int:
        """批量写入/更新向量"""
        if not points:
            return 0
        try:
            dim = len(points[0].get("vector", []))
            if dim == 0:
                return 0
            client = self._ensure_collection(collection, dim)
            data = [
                {
                    "id": str(p.get("id", "")),
                    "vector": p["vector"],
                    "content": p.get("content", "")[:65535],
                    "metadata": p.get("metadata", {}) or {},
                }
                for p in points
            ]
            client.upsert(collection_name=collection, data=data)
            return len(data)
        except Exception as e:
            log.warning("milvus upsert failed collection={} err={}", collection, e)
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
            [{id, score, content, metadata}]（score 为 Milvus COSINE 相似度）
        """
        if not query_vector:
            return []
        try:
            client = self._get_client()
            if not client.has_collection(collection):
                return []
            kwargs: dict = {
                "collection_name": collection,
                "data": [query_vector],
                "limit": top_k,
                "output_fields": ["content", "metadata"],
            }
            if filter_expr:
                kwargs["filter"] = filter_expr
            res = client.search(**kwargs)
            if not res:
                return []
            hits = res[0]
            out: list[dict] = []
            for hit in hits:
                entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
                score = hit.get("distance", 0.0) if isinstance(hit, dict) else getattr(hit, "distance", 0.0)
                out.append({
                    "id": hit.get("id", "") if isinstance(hit, dict) else getattr(hit, "id", ""),
                    "score": float(score),
                    "content": entity.get("content", ""),
                    "metadata": entity.get("metadata", {}) or {},
                })
            return out
        except Exception as e:
            log.warning("milvus search failed collection={} err={}", collection, e)
            return []

    async def delete(self, collection: str, ids: list[str]) -> int:
        """按 ID 删除向量"""
        if not ids:
            return 0
        try:
            client = self._get_client()
            if not client.has_collection(collection):
                return 0
            client.delete(collection_name=collection, ids=[str(i) for i in ids])
            return len(ids)
        except Exception as e:
            log.warning("milvus delete failed collection={} err={}", collection, e)
            return 0

    async def count(self, collection: str) -> int:
        """统计集合中文档数量"""
        try:
            client = self._get_client()
            if not client.has_collection(collection):
                return 0
            stats = client.get_collection_stats(collection_name=collection)
            return int(stats.get("row_count", 0)) if isinstance(stats, dict) else 0
        except Exception as e:
            log.warning("milvus count failed collection={} err={}", collection, e)
            return 0

    async def drop_collection(self, collection: str) -> None:
        """删除整个集合（危险操作）"""
        try:
            client = self._get_client()
            if client.has_collection(collection):
                client.drop_collection(collection_name=collection)
                log.info("Milvus collection dropped: {}", collection)
        except Exception as e:
            log.warning("milvus drop_collection failed collection={} err={}", collection, e)

    async def close(self) -> None:
        """关闭连接"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        log.info("MilvusVectorStore closed")
