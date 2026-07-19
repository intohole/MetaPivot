"""ORM 模型 - 工作流/审计/知识库/IM/配置/定时任务

Sprint 8.1: Agent 任务/步骤/事件 + 多轮对话记忆已抽离到 models_agent.py。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.models_user_skill import gen_uuid
from app.infra.db.session import Base


class WorkflowORM(Base):
    """工作流定义"""
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    trigger: Mapped[dict] = mapped_column(JSON, default=dict)
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
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(36))
    trigger_channel: Mapped[Optional[str]] = mapped_column(String(20))
    chat_id: Mapped[Optional[str]] = mapped_column(String(128))
    checkpoint_id: Mapped[Optional[str]] = mapped_column(String(128))
    error: Mapped[Optional[dict]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(default=None)


class WorkflowTemplateORM(Base):
    """工作流模板 — 团队 SOP 可复用模式

    场景：将高频办公自动化场景（每日站会提醒/周报生成/知识库查询+总结）沉淀为模板，
    用户一键实例化为 WorkflowORM，降低工作流创建门槛。
    instantiate 时基于 definition + trigger_template 创建 WorkflowORM，usage_count += 1。
    """
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    # 分类：daily/weekly/report/notification/communication 等，便于 Gallery 筛选
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False, index=True)
    # DAG 定义 {nodes, edges, variables}（与 WorkflowORM.definition 同构）
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 触发器模板：实例化时用户可覆盖（如 cron_expr、webhook 配置）
    trigger_template: Mapped[dict] = mapped_column(JSON, default=dict)
    # 输入参数 schema（JSON Schema），实例化时引导用户填写
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)  # public/private
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class AuditLogORM(Base):
    """审计日志表（留存6个月+）"""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
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
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
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
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)
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


class ScheduledTaskORM(Base):
    """定时任务表（用户对话中解析出的定时任务）

    场景：用户在 IM 对话中说"明天下午3点提醒我开会"或"工作日 9 点查询订单"，
    Agent 解析后创建定时任务，由 AsyncScheduler 在到点时触发执行。

    Phase 5 增强：
    - 支持 cron_expr（标准 5 段 cron，优先于 recurring）
    - DLQ：retry_count/max_retries/next_retry_at/last_error
      失败时 retry_count += 1，< max_retries 指数退避重试，>= max_retries 进入 DLQ

    状态机：pending → running → completed/failed/cancelled
    """
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    # 任务归属
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    chat_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    # 任务内容
    message: Mapped[str] = mapped_column(Text, nullable=False)  # 触发时执行的 message
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[Optional[str]] = mapped_column(String(255))  # 用户可读描述
    # 调度
    run_at: Mapped[Optional[datetime]] = mapped_column(index=True)  # 一次性任务的执行时间
    recurring: Mapped[str] = mapped_column(String(20), default="none")  # none/daily/weekly/monthly
    cron_expr: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # 标准 5 段 cron（优先于 recurring）
    next_run_at: Mapped[Optional[datetime]] = mapped_column(index=True)  # 下次执行时间
    last_run_at: Mapped[Optional[datetime]] = mapped_column()
    # 状态
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/completed/failed/cancelled
    # DLQ 字段（Phase 5）
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # 已重试次数
    max_retries: Mapped[int] = mapped_column(Integer, default=3)  # 最大重试次数
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(index=True)  # 下次重试时间（指数退避）
    last_error: Mapped[Optional[dict]] = mapped_column(JSON)  # 最近一次错误信息
    error: Mapped[Optional[dict]] = mapped_column(JSON)
    # 审计
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
