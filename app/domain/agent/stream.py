"""Agent 事件流管理 - SSE 推送与多订阅者支持

本模块为 thin facade，委托给 IEventBus 实现：
- 单机部署：LocalEventBus（进程内 asyncio.Queue + 历史缓存）
- 集群部署：RedisEventBus（Redis Pub/Sub 跨实例广播）

依赖注入：Service 层在 lifespan startup 阶段调用 stream_manager.set_bus(bus) 注入
具体 IEventBus 实例，避免 Domain 层直接 import Infra 层（遵守分层依赖方向）。

历史接口（保留以兼容现有调用方）：
- stream_manager.subscribe(task_id) -> asyncio.Queue
- stream_manager.unsubscribe(task_id, queue)
- await stream_manager.publish(task_id, event)
- stream_manager.mark_finished(task_id)
- stream_manager.cleanup(task_id)
"""
import asyncio
from typing import Optional

from app.domain.contracts.event_bus import IEventBus
from app.utils.logger import get_logger

log = get_logger("agent_stream")


class StreamManager:
    """事件流管理器（thin facade，由 Service 层注入 IEventBus）"""

    def __init__(self) -> None:
        self._bus: Optional[IEventBus] = None

    def set_bus(self, bus: IEventBus) -> None:
        """注入 IEventBus 实例（由 main.lifespan 在 startup 阶段调用）"""
        self._bus = bus

    def _get_bus(self) -> IEventBus:
        if self._bus is None:
            raise RuntimeError(
                "StreamManager not initialized: call set_bus() in lifespan startup"
            )
        return self._bus

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """订阅任务事件流（含历史事件补发）"""
        return self._get_bus().subscribe(task_id)

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        self._get_bus().unsubscribe(task_id, queue)

    async def publish(self, task_id: str, event: dict) -> None:
        """向所有订阅者推送事件（跨实例广播）"""
        await self._get_bus().publish(task_id, event)

    def mark_finished(self, task_id: str) -> None:
        """标记任务完成，向所有订阅者发送结束信号"""
        self._get_bus().mark_finished(task_id)

    def cleanup(self, task_id: str) -> None:
        """清理任务相关资源"""
        self._get_bus().cleanup(task_id)

    # 暴露内部状态（供 shutdown helper 使用，兼容旧调用方）
    @property
    def _subscribers(self):
        return self._get_bus()._subscribers


# 单例（保持原有 import 路径不变）
stream_manager = StreamManager()
