"""IM统一数据模型 - 跨渠道抽象"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Channel(str, Enum):
    DINGTALK = "dingtalk"
    WECOM = "wecom"
    FEISHU = "feishu"
    API = "api"


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    CARD = "card"
    MARKDOWN = "markdown"


class Sender(BaseModel):
    """统一用户对象"""
    user_id: str = Field(description="统一用户ID")
    original_id: str = Field(description="IM平台原始ID")
    name: Optional[str] = Field(default=None, description="显示名")
    is_admin: bool = Field(default=False, description="是否管理员")


class UnifiedMessage(BaseModel):
    """统一消息抽象 - 所有渠道消息转换为此格式"""
    msg_id: str = Field(description="全局唯一消息ID（前缀+原始ID）")
    channel: Channel
    chat_id: str = Field(description="统一会话ID")
    chat_type: str = Field(default="single", description="single/group")
    sender: Sender
    message_type: MessageType = MessageType.TEXT
    text: str = Field(default="", description="文本内容")
    mentions: list[str] = Field(default_factory=list, description="@的用户ID列表")
    files: list[dict] = Field(default_factory=list, description="文件列表")
    raw_payload: dict = Field(default_factory=dict, description="原始payload，调试用")
    timestamp: datetime = Field(default_factory=datetime.now)


class UnifiedCard(BaseModel):
    """统一卡片模型"""
    card_id: str = Field(description="卡片实例ID")
    chat_id: str
    title: str = ""
    content: str = ""
    buttons: list[dict] = Field(default_factory=list, description="按钮[{key,label,type}]")
    template: str = Field(default="default", description="卡片模板")
    callback_data: dict = Field(default_factory=dict)


class CardCallback(BaseModel):
    """卡片交互回调"""
    channel: Channel
    card_id: str
    user: Sender
    action: str = Field(description="用户动作key")
    action_data: dict = Field(default_factory=dict)
    chat_id: str = ""


class SendResult(BaseModel):
    """发送结果"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
