"""IVectorStore - 向量存储抽象接口

支持的实现：
- LocalVectorStore：进程内 numpy/纯 Python 余弦相似度，适合单机/小企业
- MilvusVectorStore：分布式向量库，适合超大型企业（百万级 chunk）

接口约束：
- 所有方法异步
- 向量维度由实现方决定（embed 后由实现方校验）
- collection 概念对应 Milvus collection / Local 命名空间
"""
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class IVectorStore(Protocol):
    """向量存储统一接口"""

    async def upsert(
        self,
        collection: str,
        points: list[dict],
    ) -> int:
        """批量写入/更新向量

        Args:
            collection: 集合名
            points: [{id, vector, metadata, content}]

        Returns:
            写入数量
        """
        ...

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> list[dict]:
        """向量近邻检索

        Returns:
            [{id, score, content, metadata}]
        """
        ...

    async def delete(self, collection: str, ids: list[str]) -> int:
        """按 ID 删除向量"""
        ...

    async def count(self, collection: str) -> int:
        """统计集合中文档数量"""
        ...

    async def drop_collection(self, collection: str) -> None:
        """删除整个集合（危险操作）"""
        ...
