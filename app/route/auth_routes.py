"""认证路由 - 登录、刷新令牌"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.route.depend import ok
from app.service.auth_service import (
    CurrentUser,
    authenticate,
    get_current_user,
    refresh_token,
)

router = APIRouter()


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class RefreshResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/token", summary="获取JWT令牌")
async def login(body: LoginRequest, request: Request):
    """用户名密码登录，返回 JWT"""
    user, token = await authenticate(body.username, body.password)
    return ok({
        "token": token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
    }, request)


@router.post("/refresh", summary="刷新令牌")
async def refresh(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """刷新 JWT（需要当前令牌有效，携带 tenant_id 上下文）"""
    token = await refresh_token(user.user_id, user.username, user.role, user.tenant_id)
    return ok({"token": token, "expires_in": 3600}, request)


@router.get("/me", summary="获取当前用户信息")
async def me(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """返回当前登录用户信息"""
    return ok({
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
        "tenant_id": user.tenant_id,
    }, request)
