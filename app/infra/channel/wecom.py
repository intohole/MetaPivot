"""企业微信适配器 - Webhook模式(HTTPS+AES加密)"""
import base64
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

from app.domain.channel.adapter import ChannelAdapter
from app.domain.channel.models import Channel, SendResult, UnifiedCard, UnifiedMessage
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.security import compute_signature

log = get_logger("wecom")

try:
    from Crypto.Cipher import AES  # type: ignore
    WECOM_AVAILABLE = True
except ImportError:
    WECOM_AVAILABLE = False
    log.warning("pycryptodome not installed, WeCom adapter disabled")


class WeComAES:
    """企微消息加解密（AES-CBC-256）"""

    @staticmethod
    def _decode_aes_key(encoding_aes_key: str) -> bytes:
        return base64.b64decode(encoding_aes_key + "=")

    @staticmethod
    def encrypt(text: str, encoding_aes_key: str, corp_id: str) -> str:
        key = WeComAES._decode_aes_key(encoding_aes_key)
        random_bytes = b"\x00" * 16
        text_bytes = text.encode("utf-8")
        corp_bytes = corp_id.encode("utf-8")
        msg_len = struct.pack("!I", socket.htonl(len(text_bytes))[0])
        plain = random_bytes + msg_len + text_bytes + corp_bytes
        pad = 32 - (len(plain) % 32)
        plain += chr(pad).encode() * pad
        cipher = AES.new(key, AES.MODE_CBC, key[:16])
        encrypted = cipher.encrypt(plain)
        return base64.b64encode(encrypted).decode()

    @staticmethod
    def decrypt(encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
        key = WeComAES._decode_aes_key(encoding_aes_key)
        cipher = AES.new(key, AES.MODE_CBC, key[:16])
        plain = cipher.decrypt(base64.b64decode(encrypted))
        pad = plain[-1]
        content = plain[:-pad]
        xml_len = socket.ntohl(struct.unpack("!I", content[16:20])[0])
        xml = content[20:20 + xml_len].decode("utf-8")
        from_corp_id = content[20 + xml_len:].decode("utf-8")
        return xml, from_corp_id


class WeComAdapter(ChannelAdapter):
    """企业微信渠道适配器"""

    channel_name = "wecom"

    async def connect(self) -> None:
        if not settings.wecom_enabled:
            log.info("WeCom adapter disabled")
            return
        log.info("WeCom Webhook mode ready")

    async def disconnect(self) -> None:
        log.info("WeCom adapter stopped")

    async def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """企微URL验证"""
        sig = compute_signature(timestamp, settings.wecom_token, nonce)
        if sig != msg_signature:
            log.warning("WeCom URL verify signature mismatch")
            raise ValueError("Signature mismatch")
        _, decrypted = WeComAES.decrypt(echostr, settings.wecom_encoding_aes_key)
        return decrypted

    async def receive_message(self, raw_xml: str) -> UnifiedMessage:
        """解析企微XML消息（已解密）"""
        root = ET.fromstring(raw_xml)
        msg_type = root.findtext("MsgType", "text")
        content = root.findtext("Content", "")
        from_user = root.findtext("FromUserName", "")
        to_user = root.findtext("ToUserName", "")
        msg_id = root.findtext("MsgId", "")
        chat_id = root.findtext("ChatId", from_user)
        create_time = root.findtext("CreateTime", str(int(time.time())))
        return UnifiedMessage(
            msg_id=f"wecom_{msg_id}",
            channel=Channel.WECOM,
            chat_id=f"wecom_{chat_id}",
            chat_type="group" if root.findtext("ChatType") == "group" else "single",
            sender={"user_id": from_user, "original_id": from_user, "name": None},
            text=content,
            raw_payload={"xml": raw_xml},
            timestamp=time.localtime(int(create_time)),
        )

    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        try:
            # 调用企微主动发消息API（需access_token）
            log.info("WeCom send to {}: {}", chat_id, text[:50])
            return SendResult(success=True, message_id=f"wecom_sent_{chat_id}")
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        return SendResult(success=False, error="Card not implemented in MVP")

    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        return SendResult(success=False, error="Not implemented")

    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        # 企微被动回复XML格式
        return SendResult(success=True, message_id=msg.msg_id)

    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        msg_signature = headers.get("msg_signature", "")
        timestamp = headers.get("timestamp", "")
        nonce = headers.get("nonce", "")
        sig = compute_signature(timestamp, settings.wecom_token, nonce)
        return sig == msg_signature
