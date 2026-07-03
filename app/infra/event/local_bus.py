"""LocalEventBus - 进程内事件总线（单机部署）

实现 IEventBus Protocol，等价于原 StreamManager 逻辑：
- 每个 topic 拥有独立的 asyncio.Queue 列表（支持多订阅者）
- 历史事件缓存（新订阅者可补发已完成步骤）
- 任务完成后向所有队列推送 stream_end 结束信号

适合：单机单进程部署（CACHE_BACKEND=memory）
"""
import asyncio
from collections import defaultdict

from app.utils.logger import get_logger

log = get_logger("event_local")

# 历史事件保留数（避免内存膨胀）
_HISTORY_LIMIT = 100


class LocalEventBus:
    """进程内事件总线（结构化满足 IEventBus Protocol）"""

    def __init__(self) -> None:
        # topic -> list[asyncio.Queue]
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # topic -> 历史事件缓存
        self._history: dict[str, list[dict]] = defaultdict(list)
        # topic -> 是否已完成
        self._finished: dict[str, bool] = {}

    async def init(self) -> None:
        """无外部依赖，无需初始化"""
        log.info("LocalEventBus initialized (in-process)")

    async def close(self) -> None:
        """清理所有订阅者"""
        for queues in self._subscribers.values():
            for q in queues:
                try:
                    q.put_nowait({"type": "stream_end", "data": {}})
                except asyncio.QueueFull:
                    pass
        self._subscribers.clear()
        self._history.clear()
        self._finished.clear()

    async def health(self) -> bool:
        """进程内总线始终健康"""
        return True

    def subscribe(self, topic: str) -> asyncio.Queue:
        """订阅主题，返回事件队列（含历史事件补发）"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[topic].append(queue)
        # 投递历史事件
        for event in self._history.get(topic, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        # 若已完成，投递结束信号
        if self._finished.get(topic):
            try:
                queue.put_nowait({"type": "stream_end", "data": {}})
            except asyncio.QueueFull:
                pass
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        if topic in self._subscribers:
            try:
                self._subscribers[topic].remove(queue)
            except ValueError:
                pass

    async def publish(self, topic: str, event: dict) -> None:
        """向所有本地订阅者推送事件"""
        # 缓存历史
        history = self._history[topic]
        history.append(event)
        if len(history) > _HISTORY_LIMIT:
            # 保留最后 80 条
            del history[: len(history) - 80]

        # 广播到本地队列
        dead_queues = []
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Subscriber queue full, dropping event for {}", topic)
                dead_queues.append(queue)
        for q in dead_queues:
            self._subscribers[topic].remove(q)

    def mark_finished(self, topic: str) -> None:
        """标记主题结束，向所有本地订阅者发送结束信号"""
        self._finished[topic] = True
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait({"type": "stream_end", "data": {}})
            except asyncio.QueueFull:
                pass

    def cleanup(self, topic: str) -> None:
        """清理主题相关资源"""
        self._subscribers.pop(topic, None)
        self._history.pop(topic, None)
        self._finished.pop(topic, None)
