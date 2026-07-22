"""管理路由 - 用户管理、角色、系统配置

挂在 /api/v1 下，路由内部包含 /users、/roles、/configs 子路径。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.route.depend import ok, page_params, paginate, PaginationParams
from app.service.auth_service import (
    CurrentUser,
    create_user,
    list_users,
    require_permission,
    update_user,
)
from app.service.auth_service import ROLE_PERMISSIONS

router = APIRouter()


# ============ 用户管理 ============

class UserCreateRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., min_length=6, description="密码")
    role: str = Field(default="user", description="角色 user/tenant_manager/tenant_admin")
    im_accounts: dict = Field(default_factory=dict, description="IM 账号绑定")


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=6)
    role: str | None = None
    im_accounts: dict | None = None
    status: str | None = None


@router.get("/users", summary="用户列表")
async def users(
    request: Request,
    pg: PaginationParams = Depends(page_params),
    keyword: str = "",
    role: str = "",
    user: CurrentUser = Depends(require_permission("user:read")),
):
    items, total = await list_users(pg.page, pg.page_size, keyword, role, tenant_id=user.tenant_id)
    return ok(paginate([_user_dict(u) for u in items], total, pg.page, pg.page_size), request)


@router.post("/users", status_code=201, summary="创建用户")
async def create_user_endpoint(
    body: UserCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
):
    new_user = await create_user(
        body.username, body.password, body.role, body.im_accounts, tenant_id=user.tenant_id)
    await _audit_user_op(user, "user.create", new_user.id, {"username": body.username, "role": body.role})
    return ok(_user_dict(new_user), request)


@router.put("/users/{user_id}", summary="更新用户")
async def update_user_endpoint(
    user_id: str,
    body: UserUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
):
    updated = await update_user(
        user_id,
        password=body.password,
        role=body.role,
        im_accounts=body.im_accounts,
        status_=body.status,
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
    )
    changed = {k: v for k, v in {"role": body.role, "status": body.status}.items() if v}
    if body.password:
        changed["password"] = "***"
    await _audit_user_op(user, "user.update", user_id, changed)
    return ok({"id": updated.id, "updated_at": updated.updated_at.isoformat() if updated.updated_at else None}, request)


async def _audit_user_op(operator: CurrentUser, action: str, target_user_id: str, detail: dict) -> None:
    """用户管理操作审计（带租户隔离上下文；密码等敏感值不入日志）"""
    from app.service.audit_service import audit_service
    await audit_service.log_action(
        user_id=operator.user_id, action=action,
        input_data={"target_user_id": target_user_id, **detail},
        status="success", tenant_id=operator.tenant_id,
    )


def _user_dict(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "im_accounts": u.im_accounts,
        "status": u.status,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


# ============ 角色 ============

@router.get("/roles", summary="角色列表")
async def roles(
    request: Request,
    user: CurrentUser = Depends(require_permission("user:read")),
):
    items = [
        {"id": r, "name": r, "permissions": sorted(p) if "*" not in p else ["*"], "description": ""}
        for r, p in ROLE_PERMISSIONS.items()
    ]
    return ok({"items": items}, request)


# ============ 配置 ============

@router.get("/configs", summary="配置列表")
async def list_configs(
    request: Request,
    category: str = "",
    user: CurrentUser = Depends(require_permission("config:manage")),
):
    from app.service.config_service import config_service
    items = await config_service.list_configs(category)
    return ok({"items": items}, request)


@router.put("/configs/{key}", summary="更新配置")
async def update_config(
    key: str,
    body: dict,
    request: Request,
    user: CurrentUser = Depends(require_permission("config:manage")),
):
    from app.service.config_service import config_service
    return ok(await config_service.update_config(key, body["value"]), request)


# ============ 企业管理概览 ============

@router.get("/overview", summary="企业管理概览（管理端仪表盘用）")
async def tenant_overview(
    request: Request,
    user: CurrentUser = Depends(require_permission("audit:read")),
):
    """返回当前租户的管理概览数据：用户数/活跃用户/Skill/工作流/定时任务/Agent任务/今日调用

    仅管理端（tenant_admin/tenant_manager）可访问，按 tenant_id 隔离。
    """
    from sqlalchemy import select, func
    from app.infra.db.session import get_db_session
    from app.infra.db.models_user_skill import UserORM, SkillORM
    from app.infra.db.models_core import WorkflowORM, AuditLogORM, ScheduledTaskORM
    from app.infra.db.models_agent import AgentTaskORM
    from datetime import datetime, date

    tid = user.tenant_id
    today_start = datetime.combine(date.today(), datetime.min.time())

    async with get_db_session() as session:
        user_count = await session.scalar(
            select(func.count()).select_from(UserORM).where(UserORM.tenant_id == tid))
        active_users = await session.scalar(
            select(func.count()).select_from(UserORM).where(
                UserORM.tenant_id == tid, UserORM.status == "active"))
        skill_count = await session.scalar(
            select(func.count()).select_from(SkillORM).where(
                SkillORM.tenant_id == tid, SkillORM.enabled.is_(True)))
        workflow_count = await session.scalar(
            select(func.count()).select_from(WorkflowORM).where(
                WorkflowORM.tenant_id == tid, WorkflowORM.enabled.is_(True)))
        schedule_count = await session.scalar(
            select(func.count()).select_from(ScheduledTaskORM).where(
                ScheduledTaskORM.tenant_id == tid))
        agent_task_count = await session.scalar(
            select(func.count()).select_from(AgentTaskORM).where(
                AgentTaskORM.tenant_id == tid))
        today_calls = await session.scalar(
            select(func.count()).select_from(AuditLogORM).where(
                AuditLogORM.tenant_id == tid, AuditLogORM.created_at >= today_start))

    return ok({
        "user_count": user_count or 0,
        "active_users": active_users or 0,
        "skill_count": skill_count or 0,
        "workflow_count": workflow_count or 0,
        "schedule_count": schedule_count or 0,
        "agent_task_count": agent_task_count or 0,
        "today_calls": today_calls or 0,
        "tenant_id": tid,
    }, request)
