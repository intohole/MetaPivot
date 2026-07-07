"""Webhook domain model + 目标类型枚举

domain 层不依赖 ORM，Webhook 是业务层形态。
WebhookORM → Webhook 转换在 webhook_service 中完成。
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class WebhookTargetType(str, Enum):
    """Webhook 触发目标类型"""
    WORKFLOW = "workflow"  # 触发工作流执行
    AGENT = "agent"        # 触发 Agent 任务


@dataclass
class Webhook:
    """Webhook domain model（业务层形态）"""
    id: str
    name: str
    token: str
    target_type: WebhookTargetType
    target_id: str
    enabled: bool = True
    secret: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    last_triggered_at: Optional[datetime] = None
