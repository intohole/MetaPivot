"""飞书适配器 - 长连接模式(WebSocket)"""
from typing import Any

from app.domain.channel.adapter import ChannelAdapter
from app.domain.channel.models import Channel, SendResult, UnifiedCard, UnifiedMessage
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("feishu")

try:
    import lark_oapi as lark  # type: ignore
    from lark_oapi.api.im.v1 import (  # type: ignore
        CreateMessageRequest, CreateMessageRequestBody,
        P2ImMessageReceiveV1, ReceiveMessageEventHandler,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    log.warning("lark-oapi not installed, Feishu adapter disabled")


class FeishuAdapter(ChannelAdapter):
    """飞书渠道适配器"""

    channel_name = "feishu"
    _client: Any = None
    _message_callback: Any = None

    async def connect(self) -> None:
        if not settings.feishu_enabled or not FEISHU_AVAILABLE:
            log.info("Feishu adapter disabled")
            return
        try:
            event_handler = (
                ReceiveMessageEventHandler.builder()
                .register_p2_im_message_receive_v1(self._handle_message)
                .build()
            )
            self._client = (
                lark.ws.Client(
                    settings.feishu_app_id,
                    settings.feishu_app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO,
                )
            )
            import asyncio
            asyncio.create_task(self._client.start())
            log.info("Feishu long-connection established")
        except Exception as e:
            log.exception("Feishu connect failed: {}", e)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.stop()
            self._client = None
            log.info("Feishu disconnected")

    def _handle_message(self, data: Any) -> None:
        """飞书消息回调（同步→异步转换）"""
        import asyncio
        asyncio.create_task(self._process_feishu_message(data))

    async def _process_feishu_message(self, data: Any) -> None:
        try:
            msg = await self.receive_message(data)
            await self._dispatch(msg)
        except Exception as e:
            log.exception("Feishu message process failed: {}", e)

    async def receive_message(self, raw: Any) -> UnifiedMessage:
        """转换飞书原始消息"""
        msg = raw.event.message
        sender_id = raw.event.sender.sender_id.open_id
        chat_id = msg.chat_id
        text = ""
        msg_type = msg.message_type
        if msg_type == "text":
            import json
            text = json.loads(msg.content).get("text", "")
        mentions = [m.key for m in getattr(raw.event, "mentions", []) or []]
        return UnifiedMessage(
            msg_id=f"fs_{msg.message_id}",
            channel=Channel.FEISHU,
            chat_id=f"fs_{chat_id}",
            chat_type="group" if msg.chat_type == "p2p" else "group",
            sender={"user_id": sender_id, "original_id": sender_id, "name": None},
            text=text,
            mentions=mentions,
            raw_payload=raw.dict() if hasattr(raw, "dict") else {},
        )

    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        try:
            if not self._client:
                return SendResult(success=False, error="Feishu not connected")
            # 调用飞书发消息API（简化）
            log.info("Feishu send to {}: {}", chat_id, text[:50])
            return SendResult(success=True, message_id=f"fs_sent_{chat_id}")
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        return SendResult(success=False, error="Card not implemented in MVP")

    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        return SendResult(success=False, error="Not implemented")

    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        return await self.send_message(msg.chat_id, text)

    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        return True  # 长连接模式SDK校验
