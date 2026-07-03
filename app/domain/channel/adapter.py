"""Channel适配器抽象基类"""
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

from app.domain.channel.models import (
    CardCallback,
    SendResult,
    UnifiedCard,
    UnifiedMessage,
)

# 消息回调签名：长连接适配器收到消息后回调（依赖注入，避免适配器向上依赖 Service）
MessageCallback = Callable[[UnifiedMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """渠道适配器抽象 - 各IM平台独立实现

    依赖方向：通过 on_message 回调将收到的消息推回上层，
    适配器自身不 import Service 层，避免循环依赖。
    """

    channel_name: str = "base"
    # 注入的消息回调（由 ChannelService 在注册适配器时设置）
    on_message: Optional[MessageCallback] = None

    async def _dispatch(self, msg: UnifiedMessage) -> None:
        """将消息回调给上层（无回调则记录日志后丢弃）"""
        if self.on_message is None:
            return
        await self.on_message(msg)

    @abstractmethod
    async def connect(self) -> None:
        """建立连接（长连接模式：WebSocket；Webhook模式：验证URL）"""

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""

    @abstractmethod
    async def receive_message(self, raw: dict) -> UnifiedMessage:
        """将原始消息转换为统一格式"""

    @abstractmethod
    async def send_message(self, chat_id: str, text: str, markdown: bool = False) -> SendResult:
        """发送文本消息"""

    @abstractmethod
    async def send_card(self, chat_id: str, card: UnifiedCard) -> SendResult:
        """发送卡片"""

    @abstractmethod
    async def update_card(self, card_id: str, updates: dict) -> SendResult:
        """更新卡片内容"""

    @abstractmethod
    async def reply_message(self, msg: UnifiedMessage, text: str) -> SendResult:
        """回复特定消息"""

    async def parse_callback(self, raw: dict) -> CardCallback:
        """解析卡片交互回调"""
        raise NotImplementedError(f"{self.channel_name} 不支持卡片回调")

    @abstractmethod
    async def verify_signature(self, headers: dict, body: bytes) -> bool:
        """验证IM平台签名"""


class ChannelRegistry:
    """渠道注册表 - 管理所有已连接的适配器"""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter, on_message: Optional[MessageCallback] = None) -> None:
        """注册适配器，可选注入消息回调（依赖注入）"""
        if on_message is not None:
            adapter.on_message = on_message
        self._adapters[adapter.channel_name] = adapter

    def get(self, channel: str) -> Optional[ChannelAdapter]:
        return self._adapters.get(channel)

    def list_channels(self) -> list[str]:
        return list(self._adapters.keys())

    async def connect_all(self) -> None:
        """连接所有已注册适配器"""
        for name, adapter in self._adapters.items():
            try:
                await adapter.connect()
            except Exception as e:
                # logger在utils层，这里用print避免循环依赖
                print(f"Channel {name} connect failed: {e}")

    async def disconnect_all(self) -> None:
        """断开所有适配器"""
        for name, adapter in self._adapters.items():
            try:
                await adapter.disconnect()
            except Exception as e:
                print(f"Channel {name} disconnect failed: {e}")


# 全局注册表
channel_registry = ChannelRegistry()
