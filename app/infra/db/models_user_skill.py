"""ORM 模型 - 用户/Skill/MCP/审计"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class UserORM(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    im_accounts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class SkillORM(Base):
    """Skill注册表"""
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # mcp/function/workflow
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), default="user")
    require_confirm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    last_called_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class MCPServerORM(Base):
    """MCP Server注册表"""
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)  # stdio/http
    endpoint: Mapped[Optional[str]] = mapped_column(String(512))
    args: Mapped[list] = mapped_column(JSONB, default=list)
    env: Mapped[dict] = mapped_column(JSONB, default=dict)
    auth_type: Mapped[Optional[str]] = mapped_column(String(20))
    auth_secret_ref: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="stopped", nullable=False)
    last_ping_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
