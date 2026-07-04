"""领域契约层 - Protocol 接口抽象

定义跨层通信的接口契约，遵循依赖倒置原则（DIP）：
- Domain 层声明接口（本模块）
- Infra 层提供具体实现（RedisCache/MemoryCache/MilvusVectorStore 等）
- Service 层仅依赖 Protocol，不依赖具体实现
- 通过 factory 按配置切换 backend

所有 Protocol 使用 @runtime_checkable 装饰，支持 isinstance 检查。
"""
from app.domain.contracts.cache import ICache
from app.domain.contracts.event_bus import IEventBus
from app.domain.contracts.llm import ILLMProvider
from app.domain.contracts.memory import IMemoryStore
from app.domain.contracts.scheduler import IScheduler
from app.domain.contracts.token_counter import ITokenCounter
from app.domain.contracts.vector import IVectorStore

__all__ = [
    "ICache",
    "IEventBus",
    "ILLMProvider",
    "IMemoryStore",
    "IScheduler",
    "ITokenCounter",
    "IVectorStore",
]
