"""飞书适配器 - 长连接模式(WebSocket)接收 + REST API 发送"""
import asyncio
import json
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
        P2ImMessageReceiveV1,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    log.warning("lark-oapi not installed, Feishu adapter disabled")

# chat_id 前缀（与 receive_message 中保持一致）
_CHAT_ID_PREFIX = "fs_"


class FeishuAdapter(ChannelAdapter):
    """飞书渠道适配器

    双客户端架构：
    - _ws_client: lark.ws.Client WebSocket 长连接（接收消息回调）
    - _api_client: lark.Client REST API（发送消息，token 由 SDK 自动刷新）
    """

    channel_name = "feishu"
    _ws_client: Any = None
    _api_client: Any = None
    _message_callback: Any = None

    async def connect(self) -> None:
        if not settings.feishu_enabled or not FEISHU_AVAILABLE:
            log.info("Feishu adapter disabled")
            return
        try:
            # REST API 客户端（发送消息用，token 由 SDK 自动刷新）
            self._api_client = (
                lark.Client.builder()
                .app_id(settings.feishu_app_id)
                .app_secret(settings.feishu_app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )
            # WebSocket 长连接客户端（接收消息回调）
            # EventDispatcherHandler.builder(verification_token, encrypt_key)
            # 当前长连接模式 SDK 自动校验，两参数传空字符串
            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._handle_message)
                .build()
            )
            self._ws_client = lark.ws.Client(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
                auto_reconnect=True,
            )
            asyncio.create_task(self._ws_client.start())
            log.info("Feishu connected (WS long-connection + REST API)")
        except Exception as e:
            log.exception("Feishu connect failed: {}", e)

    async def disconnect(self) -> None:
        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None
        self._api_client = None
        log.info("Feishu disconnected")

    def _handle_message(self, data: Any) -> None:
        """飞书消息回调（SDK 同步回调 → 异步任务）"""
        asyncio.create_task(self._process_feishu_message(data))

    async def _process_feishu_message(self, data: Any) -> None:
        try:
            msg = await self.receive_message(data)
            await self._dispatch(msg)
        except Exception as e:
            log.exception("Feishu message process failed: {}", e)

    async def receive_message(self, raw: Any) -> UnifiedMessage:
        """转换飞书原始消息为统一格式"""
        msg = raw.event.message
        sender_id = raw.event.sender.sender_id.open_id
        chat_id = msg.chat_id
        text = ""
        if msg.message_type == "text":
            text = json.loads(msg.content).get("text", "")
        mentions = [m.key for m in getattr(raw.event, "mentions", []) or []]
        return UnifiedMessage(
            msg_id=f"fs_{msg.message_id}",
            channel=Channel.FEISHU,
            chat_id=f"{_CHAT_ID_PREFIX}{chat_id}",
            chat_type="group" if msg.chat_type == "p2p" else "group",
            sender={"user_id": sender_id, "original_id": sender_id, "name": None},
            text=text,
            mentions=mentions,
            raw_payload=raw.dict() if hasattr(raw, "dict") else {},
        )

    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        """发送飞书消息（REST API im.v1.message.create）

        Args:
            chat_id: 统一会话 ID（fs_ 前缀），自动剥离前缀获取飞书原始 chat_id
            text: 文本内容
            markdown: 是否 markdown 格式（飞书 text 类型不渲染 markdown，降级为纯文本）
        """
        if not self._api_client:
            return SendResult(success=False, error="Feishu API client not connected")
        # 剥离 fs_ 前缀，获取飞书原始 receive_id
        receive_id = chat_id[len(_CHAT_ID_PREFIX):] if chat_id.startswith(_CHAT_ID_PREFIX) else chat_id
        # 飞书 text 消息 content 必须是 JSON 字符串 {"text": "..."}
        content = json.dumps({"text": text}, ensure_ascii=False)
        try:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            # lark_oapi 的 create 是同步调用，用 to_thread 避免阻塞事件循环
            resp = await asyncio.to_thread(self._api_client.im.v1.message.create, req)
            if resp.success():
                msg_id = getattr(resp.data, "message_id", "") if resp.data else ""
                log.info("Feishu sent to {}: msg_id={}", chat_id, msg_id)
                return SendResult(success=True, message_id=f"fs_{msg_id}")
            log.warning("Feishu send failed: code={} msg={}", resp.code, resp.msg)
            return SendResult(success=False, error=f"code={resp.code} msg={resp.msg}")
        except Exception as e:
            log.exception("Feishu send_message error: {}", e)
            return SendResult(success=False, error=str(e))

    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        return SendResult(success=False, error="Card not implemented in MVP")

    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        return SendResult(success=False, error="Not implemented")

    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        return await self.send_message(msg.chat_id, text)

    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        return True  # 长连接模式 SDK 自动校验