"""钉钉适配器 - Stream模式(WebSocket长连接)"""
from typing import Any

from app.domain.channel.adapter import ChannelAdapter
from app.domain.channel.models import (
    Channel,
    SendResult,
    UnifiedCard,
    UnifiedMessage,
)
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("dingtalk")

# 钉钉Stream SDK
try:
    from dingtalk_stream import DingTalkStreamClient, AckMessage, ChatbotHandler  # type: ignore
    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    log.warning("dingtalk-stream not installed, DingTalk adapter disabled")


class DingTalkMessageHandler(ChatbotHandler if DINGTALK_AVAILABLE else object):
    """钉钉消息处理器 - SDK回调"""

    def __init__(self, callback):
        super().__init__() if DINGTALK_AVAILABLE else None
        self._callback = callback

    async def process(self, callback):
        """SDK回调入口"""
        try:
            msg = await self._convert(callback)
            await self._callback(msg)
        except Exception as e:
            log.exception("DingTalk message process failed: {}", e)
        return AckMessage.STATUS_OK, 'OK'


class DingTalkAdapter(ChannelAdapter):
    """钉钉渠道适配器"""

    channel_name = "dingtalk"
    _client: Any = None

    async def connect(self) -> None:
        if not settings.dingtalk_enabled or not DINGTALK_AVAILABLE:
            log.info("DingTalk adapter disabled")
            return
        try:
            handler = DingTalkMessageHandler(self._on_message_received)
            self._client = DingTalkStreamClient(
                settings.dingtalk_client_id,
                settings.dingtalk_client_secret,
                handler,
            )
            await self._client.start()
            log.info("DingTalk Stream connected")
        except Exception as e:
            log.exception("DingTalk connect failed: {}", e)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.stop()
            self._client = None
            log.info("DingTalk Stream disconnected")

    async def _on_message_received(self, msg: UnifiedMessage) -> None:
        """消息回调入口，通过注入的 on_message 回调推给上层"""
        await self._dispatch(msg)

    async def receive_message(self, raw: dict) -> UnifiedMessage:
        """原始消息转换"""
        sender_id = raw.get("senderId", "")
        text = raw.get("text", {}).get("content", "").strip()
        mentions = [u.get("userId") for u in raw.get("atUsers", []) if u.get("userId")]
        return UnifiedMessage(
            msg_id=f"dt_{raw.get('msgId', '')}",
            channel=Channel.DINGTALK,
            chat_id=f"dt_{raw.get('conversationId', '')}",
            chat_type="group" if raw.get("conversationType") == "1" else "single",
            sender={"user_id": sender_id, "original_id": sender_id, "name": raw.get("senderNick")},
            text=text,
            mentions=mentions,
            raw_payload=raw,
        )

    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        try:
            if not self._client:
                return SendResult(success=False, error="DingTalk not connected")
            await self._client.send_text(chat_id, text)
            return SendResult(success=True)
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        # 简化实现：调用钉钉卡片API
        return SendResult(success=False, error="Card not implemented in MVP")

    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        return SendResult(success=False, error="Not implemented")

    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        return await self.send_message(msg.chat_id, text)

    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        # Stream模式由SDK校验
        return True
