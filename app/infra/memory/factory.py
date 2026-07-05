"""Memory store 工厂 - 按 settings.memory_backend 切换

支持部署规模：
- memory：进程内字典（开发环境，零外部依赖，重启丢失）
- db：数据库持久化（生产环境，跟随 DB_BACKEND 自动切换 SQLite/PostgreSQL）
- semantic：语义记忆（db + 向量记忆 + 事实抽取，跨会话语义召回）

返回的实例结构化满足 IMemoryStore Protocol，调用方无需关心具体实现。
"""
from typing import Optional

from app.domain.contracts.memory import IMemoryStore
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("memory_factory")

_memory_store: Optional[IMemoryStore] = None


async def get_memory_store() -> IMemoryStore:
    """获取记忆存储单例（首次调用时按配置初始化）"""
    global _memory_store
    if _memory_store is not None:
        return _memory_store

    backend = settings.memory_backend
    if backend == "memory":
        from app.infra.memory.in_memory import InMemoryMemoryStore
        _memory_store = InMemoryMemoryStore()
        log.info("Memory backend: memory (in-process, dev only)")
    elif backend == "semantic":
        # 语义记忆：DB（episodic）+ IVectorStore（semantic）+ LLM（embed/抽取）
        # 延迟 import 避免 chromadb/milvus 未安装时启动崩溃
        from app.infra.memory.semantic_memory import SemanticMemoryStore
        from app.infra.rag.factory import get_vector_store
        from app.infra.llm.provider import get_llm
        vector_store = get_vector_store()
        llm_provider = get_llm()
        _memory_store = SemanticMemoryStore(vector_store, llm_provider)
        log.info(
            "Memory backend: semantic (vector={} llm={})",
            type(vector_store).__name__, type(llm_provider).__name__,
        )
    else:
        from app.infra.memory.db_memory import DBMemoryStore
        _memory_store = DBMemoryStore()
        log.info("Memory backend: db ({})", settings.db_backend)
    return _memory_store


async def close_memory_store() -> None:
    """关闭记忆存储（应用关闭时调用）"""
    global _memory_store
    if _memory_store is not None:
        # InMemoryMemoryStore / DBMemoryStore / SemanticMemoryStore 都无需显式释放连接
        # （vector_store 连接由 close_vector_store 单独关闭）
        _memory_store = None
        log.info("Memory store closed")


async def check_memory_health() -> bool:
    """记忆存储健康检查"""
    try:
        store = await get_memory_store()
        return await store.health()
    except Exception as e:
        log.error("Memory health check failed: {}", e)
        return False
