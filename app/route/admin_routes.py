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
    role: str = Field(default="user", description="角色 user/manager/admin")
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
    items, total = await list_users(pg.page, pg.page_size, keyword, role)
    return ok(paginate([_user_dict(u) for u in items], total, pg.page, pg.page_size), request)


@router.post("/users", status_code=201, summary="创建用户")
async def create_user_endpoint(
    body: UserCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
):
    new_user = await create_user(body.username, body.password, body.role, body.im_accounts)
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
    )
    return ok({"id": updated.id, "updated_at": updated.updated_at.isoformat() if updated.updated_at else None}, request)


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
