"""IM 消息处理器 - 桥接 ChannelService 与 AgentService

职责：
1. 接收 ChannelService 派发的 UnifiedMessage
2. 调用 AgentService 启动任务
3. 订阅任务事件流，将结果回传 IM 渠道
4. HITL 时发送确认卡片到 IM
"""
import asyncio
import uuid

from app.domain.channel.models import UnifiedMessage
from app.utils.context import set_request_context
from app.utils.logger import get_logger

log = get_logger("message_handler")


async def handle_im_message(msg: UnifiedMessage) -> None:
    """IM 消息处理入口（由 ChannelService.dispatch_message 调用）"""
    # 忽略空消息或机器人自身消息
    if not msg.text.strip():
        return
    if msg.sender.is_admin and msg.text.startswith("/"):  # 命令模式（占位）
        await _handle_command(msg)
        return

    # IM 消息无 HTTP 请求上下文，生成合成 request_id 用于链路追踪
    # asyncio.create_task 会自动继承此 contextvars，后台任务日志可关联
    synthetic_rid = f"im-{msg.channel.value}-{uuid.uuid4().hex[:8]}"
    set_request_context(synthetic_rid, user_id=msg.sender.user_id)

    # 启动 Agent 任务
    from app.service.agent_service import agent_service
    try:
        result = await agent_service.start_task(
            message=msg.text,
            channel=msg.channel.value,
            chat_id=msg.chat_id,
            user_id=msg.sender.user_id,
            context={"sender_name": msg.sender.name or "", "mentions": msg.mentions},
            stream=False,
        )
        task_id = result["task_id"]
        log.info("Agent task started for msg {}: {}", msg.msg_id, task_id)
        # 审计 IM 消息接收（非阻塞）
        from app.service.audit_service import audit_service
        from app.utils.context import get_request_id
        try:
            await audit_service.log_action(
                user_id=msg.sender.user_id, action="im.message",
                task_id=task_id, input_data={
                    "channel": msg.channel.value, "chat_id": msg.chat_id,
                    "sender": msg.sender.user_id, "text_preview": msg.text[:100],
                },
                status="success", request_id=get_request_id(),
            )
        except Exception as audit_e:
            log.warning("audit im message failed: {}", audit_e)
        # 后台订阅事件流并回传 IM（继承当前 contextvars，日志带 request_id）
        asyncio.create_task(_stream_back_to_im(task_id, msg))
    except Exception as e:
        log.exception("Handle IM message failed: {}", e)
        await _reply_error(msg, f"处理失败：{e}")


async def _stream_back_to_im(task_id: str, msg: UnifiedMessage) -> None:
    """订阅任务事件流，将关键事件回传 IM"""
    from app.service.agent_service import agent_service
    try:
        async for event in agent_service.stream_task(task_id):
            event_type = event.get("type")
            data = event.get("data", {})

            if event_type == "human_confirm_required":
                await _send_confirm_card(msg, task_id, data)
            elif event_type == "final_result":
                answer = data.get("answer", "")
                if answer:
                    await _reply(msg, answer)
            elif event_type == "error":
                err_msg = data.get("message", "未知错误")
                await _reply_error(msg, f"执行出错：{err_msg}")
            elif event_type == "stream_end":
                return
    except Exception as e:
        log.exception("Stream back to IM failed: {}", e)


async def _reply(msg: UnifiedMessage, text: str) -> None:
    """通过原渠道回复"""
    from app.service.channel_service import channel_service
    try:
        result = await channel_service.reply(msg, text)
        if not result.success:
            log.warning("Reply failed: {}", result.error)
    except Exception as e:
        log.exception("Reply failed: {}", e)


async def _reply_error(msg: UnifiedMessage, error: str) -> None:
    """回复错误消息"""
    await _reply(msg, f"⚠️ {error}")


async def _send_confirm_card(msg: UnifiedMessage, task_id: str, confirm_data: dict) -> None:
    """发送 HITL 确认卡片到 IM

    简化实现：发送文本提示 + 操作说明（完整版应发送交互卡片）
    """
    tool = confirm_data.get("tool", "未知操作")
    input_data = confirm_data.get("input", {})
    text = (
        f"🔔 需要您确认操作\n"
        f"操作：{tool}\n"
        f"参数：{input_data}\n\n"
        f"任务ID：{task_id}\n"
        f"请通过管理后台或 API 确认（approve/reject/modify）"
    )
    await _reply(msg, text)


async def _handle_command(msg: UnifiedMessage) -> None:
    """处理 / 开头的命令消息（占位）"""
    await _reply(msg, "命令模式开发中...")


# 注册到 channel_service（由 main.py lifespan 调用）
async def register_to_channel_service() -> None:
    """将 handle_im_message 注册为 ChannelService 的消息处理器"""
    from app.service.channel_service import channel_service
    channel_service.register_handler(handle_im_message)
    log.info("IM message handler registered to ChannelService")
