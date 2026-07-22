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

# 角色权限矩阵（与客户端/管理端分离对齐）
# 角色命名遵循多租户终局：tenant_* 表示企业内角色，预留 platform_admin 给平台运营
# - user（客户端终端用户）：浏览 + 执行核心场景（Skill/Workflow/Knowledge/Agent）
# - tenant_manager（管理端中层）：在 user 基础上 + webhook/审计查看
# - tenant_admin（管理端企业管理员）：全权限（含用户管理/系统配置/Skill 发布）
# - platform_admin（平台运营，预留）：跨租户管理（当前未启用）
ROLE_PERMISSIONS = {
    "user": {
        "agent:chat", "knowledge:read", "knowledge:write",
        "skill:call", "skill:read",
        "workflow:read", "workflow:execute",
    },
    "tenant_manager": {
        "agent:chat", "knowledge:read", "knowledge:write",
        "skill:call", "skill:read",
        "workflow:read", "workflow:execute",
        "webhook:read", "audit:read",
    },
    "tenant_admin": {"*"},  # 企业内全权限
    # platform_admin 预留：未来跨租户管理时启用，当前与 tenant_admin 等价
    "platform_admin": {"*"},
}


class CurrentUser:
    """请求上下文中的当前用户（轻量对象，避免直接传递 ORM）

    tenant_id 是多租户隔离的核心上下文，所有 Service 层查询应基于此字段过滤。
    """

    def __init__(self, user_id: str, username: str, role: str, tenant_id: str = "default") -> None:
        self.user_id = user_id
        self.username = username
        self.role = role
        self.tenant_id = tenant_id

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
            "tenant_id": user.tenant_id,
        })
        log.info("User logged in: {} ({}) tenant={}", user.username, user.role, user.tenant_id)
        return user, token


async def refresh_token(user_id: str, username: str, role: str, tenant_id: str = "default") -> str:
    """刷新令牌（携带 tenant_id 上下文）"""
    return create_access_token({
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
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

    # tenant_id 优先取 JWT claim（性能），fallback 到 DB（兼容旧 token）
    tenant_id = payload.get("tenant_id") or user.tenant_id
    ctx = CurrentUser(user.id, user.username, user.role, tenant_id)
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
    tenant_id: str = "default",
) -> UserORM:
    """创建用户（多租户：继承创建者 tenant_id；username 全局唯一——登录不带租户参数）"""
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
            tenant_id=tenant_id,
        )
        session.add(user)
        await session.flush()
        log.info("User created: {} ({}) tenant={}", user.username, user.role, tenant_id)
        return user


async def list_users(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    role: str = "",
    tenant_id: str = "default",
) -> tuple[list[UserORM], int]:
    """分页查询用户（多租户：仅返回本租户用户）"""
    async with get_db_session() as session:
        stmt = select(UserORM).where(UserORM.tenant_id == tenant_id)
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
    tenant_id: str = "default",
    operator_id: str = "",
) -> UserORM:
    """更新用户（多租户：目标用户须同租户，否则 404 不泄漏存在性；防自锁：不能禁用/降级自己）"""
    async with get_db_session() as session:
        user = await session.get(UserORM, user_id)
        if user is None or user.tenant_id != tenant_id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "用户不存在", 404)
        if operator_id and user.id == operator_id:
            if status_ and status_ != "active":
                raise AppError(ErrorCode.VALIDATION_ERROR, "不能禁用自己的账号", 400)
            if role and role != user.role:
                raise AppError(ErrorCode.VALIDATION_ERROR, "不能修改自己的角色", 400)
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
