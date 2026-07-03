"""RedisEventBus - Redis Pub/Sub 跨实例事件总线（集群部署）

实现 IEventBus Protocol，解决多实例 SSE 广播问题：
- IM 消息到达实例 A → Agent 任务在实例 A 执行 → publish 事件
- Web SSE 客户端连接实例 B → 也能收到实例 A 发布的事件

机制：
1. publish() 同时：写入本地队列 + LPUSH 到 Redis 历史 + PUBLISH 到 Redis 频道
2. 后台监听器 PSUBSCRIBE metapivot:event:* → 收到消息后 fan-out 到本地队列
3. subscribe() 时从 Redis LIST LRANGE 补发历史事件
4. mark_finished() SET 标记 + PUBLISH stream_end

适合：多实例部署（CACHE_BACKEND=redis）
依赖：复用 ICache 的 Redis 客户端（不新建连接池）
"""
import asyncio
import json
from collections import defaultdict
from typing import Optional

import redis.asyncio as aioredis

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("event_redis")

_HISTORY_LIMIT = 100
_TOPIC_PREFIX = "metapivot:event:"      # Pub/Sub 频道
_HISTORY_PREFIX = "metapivot:history:"  # LIST 历史事件
_FINISHED_PREFIX = "metapivot:finished:"  # 完成标记
_HISTORY_TTL = 7200  # 历史保留 2 小时（避免无限增长）


class RedisEventBus:
    """Redis Pub/Sub 事件总线（结构化满足 IEventBus Protocol）"""

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._listener_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def init(self) -> None:
        """初始化 Redis 客户端 + 启动后台监听"""
        if self._client is not None:
            return
        self._client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            health_check_interval=30,
        )
        try:
            await self._client.ping()
        except Exception as e:
            log.error("RedisEventBus init failed: {}", e)
            raise
        self._stop_event.clear()
        self._listener_task = asyncio.create_task(self._listener_loop())
        log.info("RedisEventBus initialized (cluster mode)")

    async def close(self) -> None:
        """停止监听 + 关闭客户端"""
        self._stop_event.set()
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await asyncio.wait_for(self._listener_task, timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        # 通知本地订阅者退出
        for queues in self._subscribers.values():
            for q in queues:
                try:
                    q.put_nowait({"type": "stream_end", "data": {}})
                except asyncio.QueueFull:
                    pass
        self._subscribers.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        log.info("RedisEventBus closed")

    async def health(self) -> bool:
        """实际 ping Redis 探测连接活性"""
        if self._client is None:
            return False
        try:
            await self._client.ping()
            return True
        except Exception as e:
            log.warning("RedisEventBus health check failed: {}", e)
            return False

    async def _listener_loop(self) -> None:
        """后台监听 Redis Pub/Sub 消息，fan-out 到本地队列（带重连）"""
        assert self._client is not None
        while not self._stop_event.is_set():
            pubsub = self._client.pubsub()
            try:
                await pubsub.psubscribe(f"{_TOPIC_PREFIX}*")
                log.info("Event listener started, pattern={}*", _TOPIC_PREFIX)
                async for message in pubsub.listen():
                    if self._stop_event.is_set():
                        break
                    if message["type"] != "pmessage":
                        continue
                    topic = message["channel"][len(_TOPIC_PREFIX):]
                    try:
                        event = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        log.warning("Invalid event payload for {}", topic)
                        continue
                    self._fanout(topic, event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Event listener error: {}, reconnecting in 3s...", e)
            finally:
                try:
                    await pubsub.punsubscribe(f"{_TOPIC_PREFIX}*")
                    await pubsub.aclose()
                except Exception:
                    pass

            if self._stop_event.is_set():
                break
            # 退避等待后重连
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass  # 超时即重连

    def _fanout(self, topic: str, event: dict) -> None:
        """将事件推送到本地订阅者队列"""
        dead_queues = []
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.append(queue)
        for q in dead_queues:
            self._subscribers[topic].remove(q)

    def subscribe(self, topic: str) -> asyncio.Queue:
        """订阅主题：创建本地队列 + 从 Redis 补发历史事件"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[topic].append(queue)
        # 异步补发历史事件（不阻塞订阅）
        asyncio.create_task(self._replay_history(topic, queue))
        return queue

    async def _replay_history(self, topic: str, queue: asyncio.Queue) -> None:
        """从 Redis LIST 读取历史事件并补发"""
        if self._client is None:
            return
        try:
            key = f"{_HISTORY_PREFIX}{topic}"
            # LRANGE 0 -1 取全部，事件已 LTRIM 到 100 条内
            events_raw = await self._client.lrange(key, 0, -1)
            for raw in reversed(events_raw):  # LPUSH 导致倒序，需反转
                try:
                    event = json.loads(raw)
                    queue.put_nowait(event)
                except (json.JSONDecodeError, asyncio.QueueFull):
                    break
            # 检查是否已完成
            if await self._client.exists(f"{_FINISHED_PREFIX}{topic}"):
                queue.put_nowait({"type": "stream_end", "data": {}})
        except Exception as e:
            log.warning("Replay history failed for {}: {}", topic, e)

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """取消本地订阅"""
        if topic in self._subscribers:
            try:
                self._subscribers[topic].remove(queue)
            except ValueError:
                pass

    async def publish(self, topic: str, event: dict) -> None:
        """发布事件：本地 fan-out + Redis LPUSH + PUBLISH"""
        # 1. 本地立即 fan-out（低延迟）
        self._fanout(topic, event)
        # 2. Redis 持久化历史（供其他实例订阅时补发）
        if self._client is None:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False, default=str)
            key = f"{_HISTORY_PREFIX}{topic}"
            pipe = self._client.pipeline()
            pipe.lpush(key, payload)
            pipe.ltrim(key, 0, _HISTORY_LIMIT - 1)
            pipe.expire(key, _HISTORY_TTL)
            pipe.publish(f"{_TOPIC_PREFIX}{topic}", payload)
            await pipe.execute()
        except Exception as e:
            log.error("Publish to Redis failed for {}: {}", topic, e)

    def mark_finished(self, topic: str) -> None:
        """标记主题结束（异步发布 stream_end 到所有实例）"""
        if self._client is None:
            # Redis 不可用，仅本地通知
            self._fanout(topic, {"type": "stream_end", "data": {}})
            return
        asyncio.create_task(self._publish_finished(topic))

    async def _publish_finished(self, topic: str) -> None:
        """发布完成事件到 Redis"""
        assert self._client is not None
        try:
            pipe = self._client.pipeline()
            pipe.set(f"{_FINISHED_PREFIX}{topic}", "1", ex=_HISTORY_TTL)
            pipe.publish(
                f"{_TOPIC_PREFIX}{topic}",
                json.dumps({"type": "stream_end", "data": {}}),
            )
            await pipe.execute()
        except Exception as e:
            log.error("Publish finished failed for {}: {}", topic, e)
            # 兜底：本地通知
            self._fanout(topic, {"type": "stream_end", "data": {}})

    def cleanup(self, topic: str) -> None:
        """清理主题相关资源（本地 + Redis）"""
        self._subscribers.pop(topic, None)
        if self._client is not None:
            asyncio.create_task(self._cleanup_redis(topic))

    async def _cleanup_redis(self, topic: str) -> None:
        """清理 Redis 中的历史 + 完成标记"""
        assert self._client is not None
        try:
            await self._client.delete(
                f"{_HISTORY_PREFIX}{topic}",
                f"{_FINISHED_PREFIX}{topic}",
            )
        except Exception as e:
            log.warning("Cleanup Redis keys failed for {}: {}", topic, e)
