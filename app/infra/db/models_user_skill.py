"""ORM 模型 - 用户/Skill/MCP/审计/Skill自进化"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, UniqueConstraint, func
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
    # 多租户预留字段（当前所有用户归属 'default' 租户，后续企业级部署启用隔离）
    tenant_id: Mapped[str] = mapped_column(String(36), default="default", nullable=False, index=True)
    im_accounts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class SkillORM(Base):
    """Skill注册表"""
    __tablename__ = "skills"
    # Sprint 13: 名称租户内唯一（多租户 SaaS 终局：不同企业可同名 Skill，如"日报生成"）
    __table_args__ = (UniqueConstraint("name", "tenant_id", name="uq_skills_name_tenant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # mcp/function/workflow
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), default="user")
    require_confirm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    last_called_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    # Phase 3: 个人/团队 + 版本管理
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)  # 创建者 user_id
    visibility: Mapped[str] = mapped_column(String(20), default="private", nullable=False, index=True)  # private/shared
    # 多租户隔离字段（默认 'default' 租户，所有查询应按此过滤）
    tenant_id: Mapped[str] = mapped_column(String(36), default="default", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    changelog: Mapped[list] = mapped_column(JSON, default=list)  # [{version, change, at}]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class MCPServerORM(Base):
    """MCP Server注册表"""
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)  # stdio/http
    endpoint: Mapped[Optional[str]] = mapped_column(String(512))
    args: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    auth_type: Mapped[Optional[str]] = mapped_column(String(20))
    auth_secret_ref: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="stopped", nullable=False)
    last_ping_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class SkillExecutionORM(Base):
    """Skill 执行记录 — 自进化数据源（追踪成功率/失败模式/耗时）"""
    __tablename__ = "skill_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    skill_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)  # 冗余存储便于查询
    task_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)  # 关联 Agent 任务
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success/failed
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    args_summary: Mapped[Optional[dict]] = mapped_column(JSON, default=None)  # 脱敏入参摘要
    error_message: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)


class SkillRevisionORM(Base):
    """Skill 版本修订 — 自进化变更的可审计记录（PR-like Review）"""
    __tablename__ = "skill_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    skill_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    old_definition: Mapped[Optional[dict]] = mapped_column(JSON, default=None)  # 旧 input_schema+source_ref
    new_definition: Mapped[dict] = mapped_column(JSON, nullable=False)  # 新 input_schema+source_ref
    diff_summary: Mapped[str] = mapped_column(Text, nullable=False)  # 人类可读变更摘要
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # manual/auto_optimize/failure_analysis
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)  # pending/approved/rejected/auto_merged
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, default=None)  # LLM 优化理由
    created_by: Mapped[Optional[str]] = mapped_column(String(36), default=None)  # 触发者(user_id/system)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(36), default=None)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SkillDraftORM(Base):
    """Skill 草稿队列 — reflector/failure_analyzer 自动生成的待审核 Skill"""
    __tablename__ = "skill_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # workflow/function
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)  # workflow_id 或 function 名
    tags: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, default=None)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)  # reflector/failure_analyzer/manual
    task_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)  # 来源任务
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)  # pending/approved/rejected
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), default=None)
    # 多租户隔离（Sprint 13：草稿审批后转为正式 Skill 须落回来源租户）
    tenant_id: Mapped[str] = mapped_column(String(36), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
