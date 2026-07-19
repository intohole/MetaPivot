"""ORM 模型 - Agent 任务/步骤/事件 + 多轮对话记忆

Sprint 8.1: 从 models_core.py 拆离，保持 models_core.py ≤ 300 行。
包含：
- AgentTaskORM: Agent 任务主表
- AgentTaskStepORM: Agent 任务步骤表
- AgentTaskEventORM: Agent 任务事件表（会话重放/链路可见性）
- ChatMessageORM: 多轮对话消息表（跨任务记忆）
- ChatSummaryORM: 会话摘要表（长对话压缩）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.models_user_skill import gen_uuid
from app.infra.db.session import Base


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


class AgentTaskEventORM(Base):
    """Agent 任务事件表（Phase 4 会话重放 + 链路可见性）

    记录每个 Agent step 的节点级事件（step_started/llm_call/tool_call/step_completed 等），
    用于会话重放（GET /tasks/{id}/replay）和调试。
    fire-and-forget 持久化（asyncio.create_task），不阻塞主链路。
    """
    __tablename__ = "agent_task_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_data: Mapped[dict] = mapped_column(JSON, default=dict)
    step_index: Mapped[Optional[int]] = mapped_column(Integer)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)


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
