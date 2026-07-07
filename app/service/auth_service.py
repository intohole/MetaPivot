"""AuthService - 认证服务：登录、JWT 签发与校验、用户与权限查询

职责：
1. 用户名密码校验 → 签发 JWT
2. 从 JWT 解析当前用户（FastAPI 依赖）
3. RBAC 角色权限校验（admin/manager/user）
4. 用户 CRUD（管理员操作）
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models_user_skill import UserORM
from app.infra.db.session import get_db_session, get_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode
from app.utils.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

log = get_logger("auth_service")

# JWT Bearer 提取器
_bearer = HTTPBearer(auto_error=False)

# 角色权限矩阵
ROLE_PERMISSIONS = {
    "user": {"agent:chat", "knowledge:read", "skill:call"},
    "manager": {"agent:chat", "knowledge:read", "skill:call", "workflow:execute", "webhook:read"},
    "admin": {"*"},  # 全权限
}


class CurrentUser:
    """请求上下文中的当前用户（轻量对象，避免直接传递 ORM）"""

    def __init__(self, user_id: str, username: str, role: str) -> None:
        self.user_id = user_id
        self.username = username
        self.role = role

    def has_permission(self, permission: str) -> bool:
        perms = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in perms or permission in perms


async def authenticate(username: str, password: str) -> tuple[UserORM, str]:
    """用户名密码校验，返回 (user, token)"""
    async with get_db_session() as session:
        stmt = select(UserORM).where(UserORM.username == username)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            raise AppError(
                ErrorCode.AUTH_INVALID_CREDENTIALS,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        if user.status != "active":
            raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "用户已被禁用", 403)
        token = create_access_token({
            "sub": user.id,
            "username": user.username,
            "role": user.role,
        })
        log.info("User logged in: {} ({})", user.username, user.role)
        return user, token


async def refresh_token(user_id: str, username: str, role: str) -> str:
    """刷新令牌"""
    return create_access_token({
        "sub": user_id,
        "username": username,
        "role": role,
    })


async def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """FastAPI 依赖：从 JWT 解析当前用户"""
    if creds is None:
        raise HTTPException(status_code=401, detail="未提供认证凭证")
    try:
        payload = decode_access_token(creds.credentials)
    except Exception as e:
        log.warning("Token decode failed: {}", e)
        raise HTTPException(status_code=401, detail="令牌无效或已过期")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="令牌内容无效")

    stmt = select(UserORM).where(UserORM.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    ctx = CurrentUser(user.id, user.username, user.role)
    request.state.current_user = ctx
    return ctx


def require_permission(permission: str):
    """权限校验依赖工厂：require_permission("workflow:execute")"""
    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_permission(permission):
            raise AppError(
                ErrorCode.AUTH_PERMISSION_DENIED,
                status_code=403,
                details={"required": permission},
            )
        return user
    return _check


# ============ 用户 CRUD（管理员操作） ============

async def create_user(
    username: str,
    password: str,
    role: str = "user",
    im_accounts: Optional[dict] = None,
) -> UserORM:
    """创建用户"""
    async with get_db_session() as session:
        exists = await session.execute(select(UserORM).where(UserORM.username == username))
        if exists.scalar_one_or_none():
            raise AppError(ErrorCode.VALIDATION_ERROR, "用户名已存在", 409)
        if role not in ROLE_PERMISSIONS:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"非法角色: {role}", 400)
        user = UserORM(
            username=username,
            password_hash=hash_password(password),
            role=role,
            im_accounts=im_accounts or {},
            status="active",
        )
        session.add(user)
        await session.flush()
        log.info("User created: {} ({})", user.username, user.role)
        return user


async def list_users(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    role: str = "",
) -> tuple[list[UserORM], int]:
    """分页查询用户"""
    async with get_db_session() as session:
        stmt = select(UserORM)
        if keyword:
            stmt = stmt.where(UserORM.username.ilike(f"%{keyword}%"))
        if role:
            stmt = stmt.where(UserORM.role == role)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(UserORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        return result.scalars().all(), total


async def update_user(
    user_id: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    im_accounts: Optional[dict] = None,
    status_: Optional[str] = None,
) -> UserORM:
    """更新用户"""
    async with get_db_session() as session:
        user = await session.get(UserORM, user_id)
        if user is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "用户不存在", 404)
        if password:
            user.password_hash = hash_password(password)
        if role:
            if role not in ROLE_PERMISSIONS:
                raise AppError(ErrorCode.VALIDATION_ERROR, f"非法角色: {role}", 400)
            user.role = role
        if im_accounts is not None:
            user.im_accounts = im_accounts
        if status_:
            user.status = status_
        await session.flush()
        return user
