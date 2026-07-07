"""WebhookORM - 外部事件触发器持久化

外部系统（IM/Git/CI/自定义）通过 HTTP POST /api/v1/webhooks/{token}
触发 workflow 或 agent 任务。WebhookORM 存储 token → 目标映射。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.models_user_skill import gen_uuid
from app.infra.db.session import Base


class WebhookORM(Base):
    """Webhook 配置（外部系统 HTTP 触发入口）"""
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)  # workflow / agent
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    secret: Mapped[Optional[str]] = mapped_column(String(128))  # HMAC 校验密钥（可选）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
