"""LocalVectorStore - 进程内向量库（单机/小企业部署）

特性：
- 纯 Python 余弦相似度，零外部依赖（无需 Milvus）
- 进程内字典存储，重启丢失（适合开发/小规模知识库）
- 适合 < 10 万 chunk 的场景；更大规模请用 MilvusVectorStore

实现 IVectorStore 协议（app.domain.contracts.vector.IVectorStore）。
"""
import math
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("local_vector")


class LocalVectorStore:
    """本地内存向量库（结构化满足 IVectorStore Protocol）"""

    def __init__(self) -> None:
        # collection -> {id -> {vector, content, metadata}}
        self._collections: dict[str, dict[str, dict]] = {}

    async def upsert(self, collection: str, points: list[dict]) -> int:
        col = self._collections.setdefault(collection, {})
        count = 0
        for p in points:
            pid = p.get("id")
            if pid is None:
                continue
            col[pid] = {
                "vector": p["vector"],
                "content": p.get("content", ""),
                "metadata": p.get("metadata", {}),
            }
            count += 1
        return count

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> list[dict]:
        col = self._collections.get(collection, {})
        if not col:
            return []
        scored = []
        for doc_id, data in col.items():
            score = _cosine(query_vector, data["vector"])
            scored.append((score, doc_id, data))
        # 按相似度降序
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": doc_id,
                "score": score,
                "content": data["content"],
                "metadata": data["metadata"],
            }
            for score, doc_id, data in scored[:top_k]
        ]

    async def delete(self, collection: str, ids: list[str]) -> int:
        col = self._collections.get(collection, {})
        n = 0
        for i in ids:
            if col.pop(i, None) is not None:
                n += 1
        return n

    async def count(self, collection: str) -> int:
        return len(self._collections.get(collection, {}))

    async def drop_collection(self, collection: str) -> None:
        self._collections.pop(collection, None)
        log.info("Collection dropped: {}", collection)


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python 实现，避免 numpy 依赖）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
