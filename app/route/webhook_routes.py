"""Webhook 路由 - 外部触发入口 + 管理接口

路由分组：
- POST /{token}        外部事件触发（无需 JWT 鉴权，token 即凭证）
- GET  /               Webhook 列表（需 webhook:read）
- POST /               创建 Webhook（需 webhook:manage）
- DELETE /{webhook_id} 删除 Webhook（需 webhook:manage）
- POST /{webhook_id}/rotate  轮换 token（需 webhook:manage）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.route.depend import ok
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


class WebhookCreateRequest(BaseModel):
    """创建 Webhook 请求"""
    name: str = Field(..., description="Webhook 名称")
    target_type: str = Field(..., description="目标类型: workflow / agent")
    target_id: str = Field(..., description="目标 ID（workflow_id 或 agent 标识）")
    secret: str | None = Field(default=None, description="HMAC 校验密钥（可选）")


@router.post("/{token}", status_code=202, summary="外部事件触发")
async def trigger_by_webhook(
    token: str,
    request: Request,
    x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
):
    """外部系统通过 HTTP POST 触发 workflow 或 agent

    - 路径参数 token 即凭证（无需 JWT）
    - 若 Webhook 配置了 secret，需附带 X-Webhook-Signature 头（HMAC-SHA256）
    - 立即返回 202 Accepted，异步执行目标
    """
    from app.service.webhook_service import webhook_service
    payload = await request.json()
    return ok(await webhook_service.handle_incoming(token, payload, x_webhook_signature), request)


@router.get("", summary="Webhook 列表")
async def list_webhooks(
    request: Request,
    user: CurrentUser = Depends(require_permission("webhook:read")),
):
    from app.service.webhook_service import webhook_service
    return ok(await webhook_service.list_webhooks(), request)


@router.post("", status_code=201, summary="创建 Webhook")
async def create_webhook(
    body: WebhookCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("webhook:manage")),
):
    from app.service.webhook_service import webhook_service
    return ok(await webhook_service.create_webhook(
        body.name, body.target_type, body.target_id, user.user_id, body.secret,
    ), request)


@router.delete("/{webhook_id}", summary="删除 Webhook")
async def delete_webhook(
    webhook_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("webhook:manage")),
):
    from app.service.webhook_service import webhook_service
    return ok(await webhook_service.delete_webhook(webhook_id), request)


@router.post("/{webhook_id}/rotate", summary="轮换 Webhook token")
async def rotate_webhook_token(
    webhook_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("webhook:manage")),
):
    """轮换 token（旧 token 立即失效，需更新外部系统配置）"""
    from app.service.webhook_service import webhook_service
    return ok(await webhook_service.rotate_token(webhook_id), request)
