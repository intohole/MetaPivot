"""ChannelService - IM 消息统一派发与发送中枢

职责：
1. 接收各 IM 适配器推送的 UnifiedMessage，异步派发给消息处理器
2. 提供统一的跨渠道发送接口（文本/卡片/回复）
3. 持久化 IM 消息到 DB（用于审计与上下文）
4. 解耦 IM 适配器层与 Agent 层（处理器动态注册，避免循环依赖）
"""
from typing import Awaitable, Callable, Optional

from sqlalchemy import select

from app.domain.channel.adapter import channel_registry
from app.domain.channel.models import (
    Channel,
    SendResult,
    UnifiedCard,
    UnifiedMessage,
)
from app.infra.db.models_core import IMChatORM, IMMessageORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("channel_service")

# 消息处理器签名：接收 UnifiedMessage，返回 None（异步处理）
MessageHandler = Callable[[UnifiedMessage], Awaitable[None]]


class ChannelService:
    """IM 消息派发与发送服务（单例）"""

    def __init__(self) -> None:
        self._handler: Optional[MessageHandler] = None
        self._pending_tasks: set = set()  # 持有后台任务引用，避免被GC
        # Phase 双向流: IM 消息触发器（DI 注入，未注入时跳过 workflow 触发）
        self._im_trigger: Optional[object] = None

    def register_handler(self, handler: MessageHandler) -> None:
        """注册消息处理器（AgentService 初始化时调用）"""
        self._handler = handler
        log.info("Message handler registered: {}", handler.__qualname__)

    def set_im_trigger(self, im_trigger: object) -> None:
        """DI 注入 IM 消息触发器（由 main.py lifespan 调用）

        注入后每条 IM 消息会尝试匹配并触发 im_message 类型的工作流。
        未注入时跳过（向后兼容）。
        """
        self._im_trigger = im_trigger
        log.info("IMTrigger injected into ChannelService")

    async def dispatch_message(self, msg: UnifiedMessage) -> None:
        """派发 IM 消息到注册的处理器（异步非阻塞）"""
        # 持久化原始消息（即使无处理器也保留）
        await self._persist_message(msg)

        # 双向流: IM 消息 → Workflow 触发（与 Agent 处理并行，互不阻断）
        if self._im_trigger is not None:
            trigger_task = self._im_trigger.match_and_trigger(msg)
            bg_trigger = _create_background_task(trigger_task, msg.msg_id + ":im_trigger")
            self._pending_tasks.add(bg_trigger)
            bg_trigger.add_done_callback(self._pending_tasks.discard)

        if self._handler is None:
            log.warning(
                "No message handler registered, msg {} dropped",
                msg.msg_id,
            )
            return

        # 异步派发，不阻塞 IM 适配器回调
        task = self._handler(msg)
        bg = _create_background_task(task, msg.msg_id)
        self._pending_tasks.add(bg)
        bg.add_done_callback(self._pending_tasks.discard)

    async def _persist_message(self, msg: UnifiedMessage) -> None:
        """落库 IM 消息与会话"""
        try:
            async with get_db_session() as session:
                # 会话幂等
                exists = await session.execute(
                    select(IMChatORM).where(IMChatORM.id == msg.chat_id)
                )
                if exists.scalar() is None:
                    session.add(IMChatORM(
                        id=msg.chat_id,
                        channel=msg.channel.value,
                        original_chat_id=msg.chat_id,
                        chat_type=msg.chat_type,
                    ))
                session.add(IMMessageORM(
                    id=msg.msg_id,
                    channel=msg.channel.value,
                    original_msg_id=msg.msg_id,
                    chat_id=msg.chat_id,
                    sender_id=msg.sender.user_id,
                    sender_name=msg.sender.name,
                    content=msg.text,
                    message_type=msg.message_type.value,
                    raw_payload=msg.raw_payload,
                ))
        except Exception as e:
            log.exception("Persist IM message failed: {}", e)

    # ============ 统一发送接口 ============

    async def send_text(
        self,
        channel: Channel,
        chat_id: str,
        text: str,
        markdown: bool = False,
    ) -> SendResult:
        """通过指定渠道发送文本"""
        adapter = channel_registry.get(channel.value)
        if adapter is None:
            return SendResult(success=False, error=f"Channel {channel} not registered")
        return await adapter.send_message(chat_id, text, markdown)

    async def send_card(
        self,
        channel: Channel,
        chat_id: str,
        card: UnifiedCard,
    ) -> SendResult:
        """通过指定渠道发送卡片"""
        adapter = channel_registry.get(channel.value)
        if adapter is None:
            return SendResult(success=False, error=f"Channel {channel} not registered")
        return await adapter.send_card(chat_id, card)

    async def reply(
        self,
        msg: UnifiedMessage,
        text: str,
    ) -> SendResult:
        """回复原始消息"""
        adapter = channel_registry.get(msg.channel.value)
        if adapter is None:
            return SendResult(success=False, error=f"Channel {msg.channel} not registered")
        return await adapter.reply_message(msg, text)

    async def update_card(
        self,
        channel: Channel,
        card_id: str,
        updates: dict,
    ) -> SendResult:
        """更新已发送的卡片内容"""
        adapter = channel_registry.get(channel.value)
        if adapter is None:
            return SendResult(success=False, error=f"Channel {channel} not registered")
        return await adapter.update_card(card_id, updates)

    def list_active_channels(self) -> list[str]:
        """获取已注册渠道列表"""
        return channel_registry.list_channels()


def _create_background_task(coro, msg_id: str):
    """创建后台任务并附加异常回调"""
    import asyncio

    async def _wrapped():
        try:
            await coro
        except Exception as e:
            log.exception("Message handler failed for {}: {}", msg_id, e)

    return asyncio.create_task(_wrapped())


# 全局单例
channel_service = ChannelService()
