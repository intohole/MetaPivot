"""Agent 事件流管理 - SSE 推送与多订阅者支持

设计：
- 每个 task_id 拥有独立的事件流
- 多个 SSE 客户端可订阅同一 task（如 IM 卡片 + Web 后台同时订阅）
- 历史事件缓存（新订阅者可收到已完成步骤的事件）
- 任务完成后自动清理
"""
import asyncio
from collections import defaultdict

from app.utils.logger import get_logger

log = get_logger("agent_stream")

# 历史事件保留数（避免内存膨胀）
_HISTORY_LIMIT = 100


class StreamManager:
    """事件流管理器（单例）"""

    def __init__(self) -> None:
        # task_id -> list[asyncio.Queue]
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # task_id -> 历史事件缓存
        self._history: dict[str, list[dict]] = defaultdict(list)
        # task_id -> 是否已完成
        self._finished: dict[str, bool] = {}

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """订阅任务事件流"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[task_id].append(queue)
        # 投递历史事件
        for event in self._history.get(task_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        # 若已完成，投递结束信号
        if self._finished.get(task_id):
            try:
                queue.put_nowait({"type": "stream_end", "data": {}})
            except asyncio.QueueFull:
                pass
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        if task_id in self._subscribers:
            try:
                self._subscribers[task_id].remove(queue)
            except ValueError:
                pass

    async def publish(self, task_id: str, event: dict) -> None:
        """向所有订阅者推送事件"""
        # 缓存历史
        history = self._history[task_id]
        history.append(event)
        if len(history) > _HISTORY_LIMIT:
            # 保留最后 80 条
            del history[: len(history) - 80]

        # 广播
        dead_queues = []
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Subscriber queue full, dropping event for {}", task_id)
                dead_queues.append(queue)
        for q in dead_queues:
            self._subscribers[task_id].remove(q)

    def mark_finished(self, task_id: str) -> None:
        """标记任务完成，向所有订阅者发送结束信号"""
        self._finished[task_id] = True
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait({"type": "stream_end", "data": {}})
            except asyncio.QueueFull:
                pass

    def cleanup(self, task_id: str) -> None:
        """清理任务相关资源"""
        self._subscribers.pop(task_id, None)
        self._history.pop(task_id, None)
        self._finished.pop(task_id, None)


stream_manager = StreamManager()
