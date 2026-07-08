"""IMTriggerService - IM 消息 → Workflow 自动触发桥接

职责：
1. 查询所有 enabled 且 trigger.type == "im_message" 的工作流
2. 匹配消息文本（关键词 substring，大小写不敏感）
3. 匹配会话过滤器（im_chat_filter 限定特定会话）
4. 触发匹配的 workflow（fire-and-forget，不阻塞消息派发）

设计：
- 由 main.py lifespan 初始化，DI 注入到 ChannelService
- ChannelService.dispatch_message 持久化消息后调用 match_and_trigger
- 与 Agent 消息处理器并行（IM 消息可同时触发 Agent + Workflow）
- 异常隔离：任何 workflow 触发失败只记日志，不影响消息派发主链路
"""
import asyncio
from typing import Optional

from sqlalchemy import select

from app.domain.channel.models import UnifiedMessage
from app.domain.workflow.trigger_spec import TriggerSpec
from app.infra.db.models_core import WorkflowORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("im_trigger_service")


class IMTriggerService:
    """IM 消息触发 Workflow 服务（单例）

    通过 DI（set_im_trigger）注入到 ChannelService。
    """

    async def match_and_trigger(self, msg: UnifiedMessage) -> list[dict]:
        """匹配 IM 消息 → 触发对应 workflow

        Args:
            msg: 统一 IM 消息

        Returns:
            触发结果列表 [{"workflow_id", "execution_id", "workflow_name"}]
            异常时返回空列表（不阻断主链路）
        """
        if not msg.text or not msg.text.strip():
            return []

        # 查询所有 enabled 的 im_message 触发工作流
        specs = await self._list_im_trigger_workflows()
        if not specs:
            return []

        text_lower = msg.text.lower()
        triggered = []
        for wf_id, wf_name, spec in specs:
            if not self._match(msg, text_lower, spec):
                continue
            try:
                exec_info = await self._trigger_workflow(wf_id, wf_name, msg)
                triggered.append(exec_info)
            except Exception as e:
                log.warning(
                    "IM trigger workflow {} failed for msg {}: {}",
                    wf_id, msg.msg_id, e,
                )
        if triggered:
            log.info(
                "IM msg {} triggered {} workflow(s): {}",
                msg.msg_id, len(triggered),
                [t["workflow_name"] for t in triggered],
            )
        return triggered

    async def _list_im_trigger_workflows(self) -> list[tuple[str, str, TriggerSpec]]:
        """查询所有 enabled 且 trigger.type == im_message 的工作流

        Returns:
            [(workflow_id, workflow_name, TriggerSpec), ...]
        """
        from app.domain.workflow.trigger_spec import parse_trigger
        async with get_db_session() as session:
            stmt = select(WorkflowORM).where(WorkflowORM.enabled.is_(True))
            items = (await session.execute(stmt)).scalars().all()
            result = []
            for wf in items:
                trigger_dict = wf.trigger or {}
                if trigger_dict.get("type") != "im_message":
                    continue
                try:
                    spec = parse_trigger(trigger_dict)
                    if spec.im_keyword:
                        result.append((wf.id, wf.name, spec))
                except Exception as e:
                    log.warning("Parse trigger for workflow {} failed: {}", wf.id, e)
            return result

    @staticmethod
    def _match(msg: UnifiedMessage, text_lower: str, spec: TriggerSpec) -> bool:
        """匹配消息是否命中触发规则

        规则：
        1. 消息文本包含关键词（substring，大小写不敏感）
        2. 若 spec.im_chat_filter 非空，会话 ID 必须匹配
        """
        keyword = (spec.im_keyword or "").lower()
        if keyword not in text_lower:
            return False
        if spec.im_chat_filter and msg.chat_id != spec.im_chat_filter:
            return False
        return True

    async def _trigger_workflow(
        self, workflow_id: str, workflow_name: str, msg: UnifiedMessage,
    ) -> dict:
        """触发工作流执行（fire-and-forget）"""
        from app.service.workflow_service import workflow_service
        inputs = {
            "message": msg.text,
            "sender_id": msg.sender.user_id,
            "sender_name": msg.sender.name or "",
            "chat_id": msg.chat_id,
            "channel": msg.channel.value,
            "msg_id": msg.msg_id,
        }
        # 异步触发，不阻塞消息派发
        result = await workflow_service.execute_workflow(
            workflow_id=workflow_id,
            inputs=inputs,
            chat_id=msg.chat_id,
            user_id=msg.sender.user_id,
        )
        return {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "execution_id": result.get("execution_id", ""),
        }


# 全局单例
im_trigger_service = IMTriggerService()
