"""WebhookService - 外部事件触发器 CRUD + 入口路由

职责：
1. Webhook CRUD（create/list/delete/rotate_token）
2. token 生成（secrets.token_urlsafe(24)）
3. handle_incoming：鉴权（token + 可选 HMAC）→ 路由到 workflow/agent
4. HMAC-SHA256 签名校验（secret 非空时校验 X-Webhook-Signature）

设计：
- 接收立即返回 202，异步执行（不阻塞外部系统）
- target_type=workflow → workflow_service.execute_workflow
- target_type=agent → agent_service.start_task
- 失败通过 audit_log 追踪
"""
import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.domain.webhook.models import WebhookTargetType
from app.infra.db.models_webhook import WebhookORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("webhook_service")


class WebhookService:
    """Webhook 服务单例"""

    async def create_webhook(
        self, name: str, target_type: str, target_id: str,
        created_by: str = "", secret: Optional[str] = None,
    ) -> dict:
        """创建 Webhook，生成唯一 token"""
        if target_type not in ("workflow", "agent"):
            raise AppError(ErrorCode.VALIDATION_ERROR, f"target_type 非法: {target_type}", 400)
        token = secrets.token_urlsafe(24)
        async with get_db_session() as session:
            hook = WebhookORM(
                name=name, token=token, target_type=target_type,
                target_id=target_id, secret=secret,
                created_by=created_by or None,
            )
            session.add(hook)
            await session.flush()
            log.info("Webhook created: {} ({}) target={}:{}", name, hook.id, target_type, target_id)
            return {
                "id": hook.id, "name": hook.name, "token": token,
                "target_type": target_type, "target_id": target_id,
                "created_at": hook.created_at.isoformat() if hook.created_at else None,
            }

    async def list_webhooks(self) -> list[dict]:
        """列出所有 Webhook（token 脱敏）"""
        async with get_db_session() as session:
            stmt = select(WebhookORM).order_by(WebhookORM.created_at.desc())
            items = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(h, mask_token=True) for h in items]

    async def delete_webhook(self, webhook_id: str) -> dict:
        async with get_db_session() as session:
            hook = await session.get(WebhookORM, webhook_id)
            if hook is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "Webhook 不存在", 404)
            await session.delete(hook)
            return {"id": webhook_id, "deleted": True}

    async def rotate_token(self, webhook_id: str) -> dict:
        """轮换 token（旧 token 立即失效）"""
        new_token = secrets.token_urlsafe(24)
        async with get_db_session() as session:
            hook = await session.get(WebhookORM, webhook_id)
            if hook is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "Webhook 不存在", 404)
            hook.token = new_token
            await session.flush()
            return {"id": webhook_id, "token": new_token}

    async def handle_incoming(self, token: str, payload: dict, signature: Optional[str] = None) -> dict:
        """处理外部触发请求：鉴权 → 路由到 workflow/agent

        Returns:
            {"status": "accepted", "target_type": ..., "target_id": ...}
        """
        hook = await self._find_by_token(token)
        if hook is None or not hook.enabled:
            raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "无效或已禁用的 webhook token", 401)

        # HMAC 签名校验（secret 非空时强制）
        if hook.secret:
            if not signature or not self._verify_signature(hook.secret, payload, signature):
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "签名校验失败", 401)

        # 更新最后触发时间
        await self._touch_triggered(hook.id)

        # 路由到目标
        if hook.target_type == WebhookTargetType.WORKFLOW.value:
            from app.service.workflow_service import workflow_service
            import asyncio
            asyncio.create_task(workflow_service.execute_workflow(
                workflow_id=hook.target_id, inputs=payload,
                chat_id=f"webhook:{hook.id}", user_id=hook.created_by or "",
            ))
        elif hook.target_type == WebhookTargetType.AGENT.value:
            from app.service.agent_service import agent_service
            message = payload.get("message", "") if isinstance(payload, dict) else str(payload)
            import asyncio
            asyncio.create_task(agent_service.start_task(
                message=message, channel="webhook",
                chat_id=f"webhook:{hook.id}", user_id=hook.created_by or "",
                context={"webhook_id": hook.id, "payload": payload},
            ))
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"未知 target_type: {hook.target_type}", 400)

        log.info("Webhook {} triggered {}:{}, payload keys={}", hook.id, hook.target_type, hook.target_id,
                 list(payload.keys()) if isinstance(payload, dict) else "non-dict")
        return {"status": "accepted", "target_type": hook.target_type, "target_id": hook.target_id}

    # ============ 内部 helper ============

    async def _find_by_token(self, token: str) -> Optional[WebhookORM]:
        async with get_db_session() as session:
            stmt = select(WebhookORM).where(WebhookORM.token == token)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def _touch_triggered(self, webhook_id: str) -> None:
        from sqlalchemy import update as sa_update
        async with get_db_session() as session:
            await session.execute(
                sa_update(WebhookORM).where(WebhookORM.id == webhook_id)
                .values(last_triggered_at=datetime.now())
            )

    @staticmethod
    def _verify_signature(secret: str, payload: dict, signature: str) -> bool:
        """HMAC-SHA256 签名校验

        signature 格式：sha256=<hex>（GitHub 风格）或纯 hex
        """
        import json
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        provided = signature.split("=", 1)[-1] if "=" in signature else signature
        return hmac.compare_digest(expected, provided)

    @staticmethod
    def _to_dict(h: WebhookORM, mask_token: bool = False) -> dict:
        token = h.token
        if mask_token and len(token) > 8:
            token = token[:4] + "****" + token[-4:]
        return {
            "id": h.id, "name": h.name, "token": token,
            "target_type": h.target_type, "target_id": h.target_id,
            "enabled": h.enabled, "created_by": h.created_by,
            "created_at": h.created_at.isoformat() if h.created_at else None,
            "last_triggered_at": h.last_triggered_at.isoformat() if h.last_triggered_at else None,
        }


webhook_service = WebhookService()
