"""企业微信适配器 - Webhook模式(HTTPS+AES加密)接收 + 主动发消息API发送"""
import asyncio
import base64
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx

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

# 企微 API 基础地址
_WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
# access_token 提前刷新阈值（避免临界过期，提前 5 分钟刷新）
_TOKEN_REFRESH_MARGIN = 300
# chat_id 前缀（与 receive_message 保持一致）
_CHAT_ID_PREFIX = "wecom_"


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
    """企业微信渠道适配器

    发消息流程：获取 access_token（缓存 2h）→ POST /message/send
    收消息流程：Webhook 回调（XML + AES 加解密）
    """

    channel_name = "wecom"
    _access_token: str = ""
    _token_expires_at: float = 0.0
    _token_lock: Optional[asyncio.Lock] = None

    def _ensure_lock(self) -> asyncio.Lock:
        """延迟初始化 Lock（避免事件循环未启动时创建）"""
        if self._token_lock is None:
            self._token_lock = asyncio.Lock()
        return self._token_lock

    async def connect(self) -> None:
        if not settings.wecom_enabled or not WECOM_AVAILABLE:
            log.info("WeCom adapter disabled")
            return
        log.info("WeCom Webhook mode ready (receive) + send API armed")

    async def disconnect(self) -> None:
        log.info("WeCom adapter stopped")

    async def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """企微 URL 验证"""
        sig = compute_signature(timestamp, settings.wecom_token, nonce)
        if sig != msg_signature:
            log.warning("WeCom URL verify signature mismatch")
            raise ValueError("Signature mismatch")
        _, decrypted = WeComAES.decrypt(echostr, settings.wecom_encoding_aes_key)
        return decrypted

    async def receive_message(self, raw_xml: str) -> UnifiedMessage:
        """解析企微 XML 消息（已解密）"""
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
            chat_id=f"{_CHAT_ID_PREFIX}{chat_id}",
            chat_type="group" if root.findtext("ChatType") == "group" else "single",
            sender={"user_id": from_user, "original_id": from_user, "name": None},
            text=content,
            raw_payload={"xml": raw_xml},
            timestamp=time.localtime(int(create_time)),
        )

    async def _get_access_token(self) -> tuple[str, str]:
        """获取企微 access_token（带缓存 + 并发锁）

        Returns:
            (access_token, error_msg) — error_msg 非空表示获取失败
        """
        # 快速路径：缓存有效直接返回（无锁）
        now = time.time()
        if self._access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN:
            return self._access_token, ""

        async with self._ensure_lock():
            # double-check：持锁后再检查一次（避免队列中多个任务重复获取）
            now = time.time()
            if self._access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN:
                return self._access_token, ""

            url = f"{_WECOM_API_BASE}/gettoken"
            params = {
                "corpid": settings.wecom_corp_id,
                "corpsecret": settings.wecom_app_secret,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(url, params=params)
                    data = r.json()
            except Exception as e:
                log.exception("WeCom gettoken network error: {}", e)
                return "", f"network error: {e}"

            if data.get("errcode", 0) != 0:
                log.warning("WeCom gettoken failed: {} {}", data.get("errcode"), data.get("errmsg"))
                return "", f"errcode={data.get('errcode')} msg={data.get('errmsg')}"

            self._access_token = data["access_token"]
            self._token_expires_at = now + int(data.get("expires_in", 7200))
            log.info("WeCom access_token refreshed, expires_in={}s", data.get("expires_in"))
            return self._access_token, ""

    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        """发送企微消息（POST /message/send）

        Args:
            chat_id: 统一会话 ID（wecom_ 前缀），剥离前缀获取企微 userid 作为 touser
            text: 文本内容
            markdown: True 时用 markdown msgtype（支持 **粗体**、<a>链接</a> 等语法）
        """
        if not settings.wecom_enabled:
            return SendResult(success=False, error="WeCom not enabled")

        # 剥离 wecom_ 前缀，获取企微原始 userid
        touser = chat_id[len(_CHAT_ID_PREFIX):] if chat_id.startswith(_CHAT_ID_PREFIX) else chat_id

        token, err = await self._get_access_token()
        if err:
            return SendResult(success=False, error=f"get token failed: {err}")

        msg_type = "markdown" if markdown else "text"
        payload = {
            "touser": touser,
            "msgtype": msg_type,
            "agentid": settings.wecom_agent_id,
            msg_type: {"content": text},
        }
        url = f"{_WECOM_API_BASE}/message/send"
        params = {"access_token": token}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, params=params, json=payload)
                data = r.json()
        except Exception as e:
            log.exception("WeCom send_message network error: {}", e)
            return SendResult(success=False, error=str(e))

        errcode = data.get("errcode", 0)
        if errcode == 0:
            msg_id = data.get("msgid", "")
            log.info("WeCom sent to {}: msgid={}", chat_id, msg_id)
            return SendResult(success=True, message_id=f"wecom_{msg_id}")

        # token 过期（42001）→ 强制刷新重试一次
        if errcode in (42001, 40014):
            log.warning("WeCom token expired ({}), force refresh + retry", errcode)
            self._token_expires_at = 0.0
            token, err = await self._get_access_token()
            if err:
                return SendResult(success=False, error=f"token refresh failed: {err}")
            params = {"access_token": token}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.post(url, params=params, json=payload)
                    data = r.json()
            except Exception as e:
                return SendResult(success=False, error=str(e))
            # 重试后重新读取 errcode/errmsg，避免用旧 errcode 配新 errmsg 误导排查
            errcode = data.get("errcode", 0)
            if errcode == 0:
                return SendResult(success=True, message_id=f"wecom_{data.get('msgid', '')}")

        log.warning("WeCom send failed: {} {}", errcode, data.get("errmsg"))
        return SendResult(success=False, error=f"errcode={errcode} msg={data.get('errmsg')}")

    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        return SendResult(success=False, error="Card not implemented in MVP")

    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        return SendResult(success=False, error="Not implemented")

    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        # 企微被动回复走 XML（24h 内有效），主动发送走 send_message
        return await self.send_message(msg.chat_id, text)

    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        msg_signature = headers.get("msg_signature", "")
        timestamp = headers.get("timestamp", "")
        nonce = headers.get("nonce", "")
        sig = compute_signature(timestamp, settings.wecom_token, nonce)
        return sig == msg_signature
