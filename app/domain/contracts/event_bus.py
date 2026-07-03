"""IEventBus - 事件总线抽象接口

用于 Agent SSE 事件广播，支持单机/集群两种部署：

- LocalEventBus：进程内 asyncio.Queue + 历史缓存，适合单机部署（零依赖）
- RedisEventBus：Redis Pub/Sub + Redis LIST 历史，适合多实例集群部署
  （IM 入口在实例 A，Web SSE 客户端连实例 B 也能收到事件）

接口约束：
- subscribe() 同步返回 asyncio.Queue（调用方通过 await queue.get() 消费）
- publish() 异步，向所有订阅者（含跨实例）广播
- mark_finished() 同步，仅本地通知（跨实例通过 publish stream_end 事件）
- 历史 events 有限保留（避免内存膨胀），新订阅者可补发
"""
import asyncio
from typing import Protocol, runtime_checkable


@runtime_checkable
class IEventBus(Protocol):
    """事件总线统一接口"""

    def subscribe(self, topic: str) -> asyncio.Queue:
        """订阅主题，返回事件队列（含历史事件补发）"""
        ...

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        ...

    async def publish(self, topic: str, event: dict) -> None:
        """发布事件到主题（跨实例广播）"""
        ...

    def mark_finished(self, topic: str) -> None:
        """标记主题结束（仅本地通知订阅者）"""
        ...

    def cleanup(self, topic: str) -> None:
        """清理主题相关资源"""
        ...

    async def init(self) -> None:
        """初始化（启动后台监听等）"""
        ...

    async def close(self) -> None:
        """关闭资源"""
        ...

    async def health(self) -> bool:
        """健康检查（实际探测连接活性，非 None 检查）"""
        ...
