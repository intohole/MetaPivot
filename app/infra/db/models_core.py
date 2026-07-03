"""ORM 模型 - 工作流/Agent任务/审计/知识库/IM"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.models_user_skill import gen_uuid
from app.infra.db.session import Base


class WorkflowORM(Base):
    """工作流定义"""
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class WorkflowExecutionORM(Base):
    """工作流执行实例"""
    __tablename__ = "workflow_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    current_node: Mapped[Optional[str]] = mapped_column(String(128))
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    outputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(36))
    trigger_channel: Mapped[Optional[str]] = mapped_column(String(20))
    chat_id: Mapped[Optional[str]] = mapped_column(String(128))
    checkpoint_id: Mapped[Optional[str]] = mapped_column(String(128))
    error: Mapped[Optional[dict]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(default=None)


class AgentTaskORM(Base):
    """Agent任务表"""
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    chat_id: Mapped[Optional[str]] = mapped_column(String(128))
    original_message: Mapped[Optional[str]] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20), default="agent")
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    plan: Mapped[Optional[dict]] = mapped_column(JSONB)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    checkpoint_id: Mapped[Optional[str]] = mapped_column(String(128))
    error: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentTaskStepORM(Base):
    """Agent任务步骤表"""
    __tablename__ = "agent_task_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[Optional[str]] = mapped_column(String(128))
    tool_name: Mapped[Optional[str]] = mapped_column(String(128))
    tool_input: Mapped[Optional[dict]] = mapped_column(JSONB)
    tool_output: Mapped[Optional[dict]] = mapped_column(JSONB)
    require_confirm: Mapped[bool] = mapped_column(Boolean, default=False)
    confirm_decision: Mapped[Optional[str]] = mapped_column(String(20))
    confirm_user: Mapped[Optional[str]] = mapped_column(String(36))
    confirm_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AuditLogORM(Base):
    """审计日志表（留存6个月+）"""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    skill_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(String(36))
    task_id: Mapped[Optional[str]] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_summary: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)


class KnowledgeDocumentORM(Base):
    """知识库文档表"""
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[Optional[str]] = mapped_column(String(512))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class IMChatORM(Base):
    """IM会话表"""
    __tablename__ = "im_chats"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    original_chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class IMMessageORM(Base):
    """IM消息记录表"""
    __tablename__ = "im_messages"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    original_msg_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sender_name: Mapped[Optional[str]] = mapped_column(String(128))
    content: Mapped[Optional[str]] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)


class ConfigORM(Base):
    """系统配置表"""
    __tablename__ = "configs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    updatable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
