"""ORM 模型 - 工作流/Agent任务/审计/知识库/IM"""
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
    plan: Mapped[Optional[dict]] = mapped_column(JSON)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    checkpoint_id: Mapped[Optional[str]] = mapped_column(String(128))
    error: Mapped[Optional[dict]] = mapped_column(JSON)
    # 链路可见性：request_id/trace_id 跨任务关联日志 + 审计
    request_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # Token 用量持久化（LLM 成本追踪）
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    # 执行耗时（P50/P99 分析）
    started_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentTaskStepORM(Base):
    """Agent任务步骤表"""
    __tablename__ = "agent_task_steps"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[Optional[str]] = mapped_column(String(128))
    tool_name: Mapped[Optional[str]] = mapped_column(String(128))
    tool_input: Mapped[Optional[dict]] = mapped_column(JSON)
    tool_output: Mapped[Optional[dict]] = mapped_column(JSON)
    require_confirm: Mapped[bool] = mapped_column(Boolean, default=False)
    confirm_decision: Mapped[Optional[str]] = mapped_column(String(20))
    confirm_user: Mapped[Optional[str]] = mapped_column(String(36))
    confirm_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # 拆分 LLM vs 工具耗时（性能瓶颈定位）
    llm_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    tool_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # Token 用量持久化（prompt/completion/total）
    token_usage: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


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


class ChatMessageORM(Base):
    """多轮对话消息表（跨任务记忆）

    用于 Agent 多轮记忆：每次会话的 user/assistant/system/tool 消息持久化，
    下次任务创建时通过 chat_id 加载历史，实现"刚才那个"等多轮对话场景。
    """
    __tablename__ = "chat_messages"

    # with_variant(Integer, "sqlite") 让 SQLite 用 INTEGER PRIMARY KEY 触发 autoincrement
    # PostgreSQL 用 BIGINT 满足大流量
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)


class ChatSummaryORM(Base):
    """会话摘要表（长对话压缩）

    当对话超过阈值时，LLM 总结历史消息为摘要，避免无限增长 + 节省 token。
    """
    __tablename__ = "chat_summaries"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class ScheduledTaskORM(Base):
    """定时任务表（用户对话中解析出的定时任务）

    场景：用户在 IM 对话中说"明天下午3点提醒我开会"或"每天早上9点查询订单状态"，
    Agent 解析后创建定时任务，由 AsyncScheduler 在到点时触发执行。

    支持一次性（run_at 指定，recurring="none"）和周期性（recurring="daily/weekly/monthly"）。
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
    next_run_at: Mapped[Optional[datetime]] = mapped_column(index=True)  # 下次执行时间（周期性）
    last_run_at: Mapped[Optional[datetime]] = mapped_column()
    # 状态
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/completed/failed/cancelled
    error: Mapped[Optional[dict]] = mapped_column(JSON)
    # 审计
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
