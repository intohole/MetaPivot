"""向量库工厂 - 按 settings.vector_backend 切换

支持部署规模：
- local：进程内余弦相似度（小企业/开发环境，零外部依赖）
- milvus：分布式向量库（百万级 chunk，超大型企业）

返回的实例结构化满足 IVectorStore Protocol。
"""
from typing import Optional

from app.domain.contracts.vector import IVectorStore
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("vector_factory")

_vector_store: Optional[IVectorStore] = None


def get_vector_store() -> IVectorStore:
    """获取向量库单例（按配置初始化）"""
    global _vector_store
    if _vector_store is None:
        if settings.vector_backend == "milvus":
            from app.infra.rag.milvus_vector import MilvusVectorStore
            _vector_store = MilvusVectorStore()
            log.info("Vector backend: milvus")
        else:
            from app.infra.rag.local_vector import LocalVectorStore
            _vector_store = LocalVectorStore()
            log.info("Vector backend: local")
    return _vector_store


async def close_vector_store() -> None:
    """关闭向量库连接（应用关闭时调用）"""
    global _vector_store
    if _vector_store is not None:
        close_method = getattr(_vector_store, "close", None)
        if close_method is not None:
            await close_method()
        _vector_store = None
        log.info("Vector store closed")
