"""IMPushService - IM 消息主动推送中枢

职责：
1. 工作流执行结果回传 IM（Sprint 6.3 双向回调）
2. 定时任务触发后等待 Agent 结果并推送（Sprint 6.4 定时推送）
3. 即时通知推送（不等待结果，仅通知任务已触发）

设计：
- 统一入口：workflow_service / async_scheduler 调用，封装格式化 + 渠道发送
- 渐进增强：任何失败只记日志，不阻断主流程
- 复用 channel_service.send_text 统一发送接口
"""
import asyncio
import json
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("im_push_service")

# 等待 Agent 任务结果的最大超时（秒）
_AGENT_WAIT_TIMEOUT = 120
# 推送消息最大长度（IM 消息不宜过长）
_MAX_MSG_LEN = 1500


class IMPushService:
    """IM 主动推送服务（单例）"""

    # ============ Sprint 6.3: 工作流结果回传 ============

    async def push_workflow_result(
        self,
        workflow_id: str,
        chat_id: str,
        inputs: dict,
        outputs: dict,
        final_status: str,
    ) -> bool:
        """工作流执行结果回传 IM 会话

        条件：inputs["channel"] 存在（标识 IM 渠道）+ workflow trigger im_callback != False
        """
        channel_str = inputs.get("channel", "")
        if not channel_str or not chat_id:
            return False  # 非 IM 触发

        if not await self._workflow_callback_enabled(workflow_id):
            return False

        wf_name = await self._get_workflow_name(workflow_id)
        message = self._format_workflow_message(wf_name, final_status, outputs, inputs)
        return await self._send_to_im(channel_str, chat_id, message)

    async def _workflow_callback_enabled(self, workflow_id: str) -> bool:
        """查询 workflow trigger 是否启用 IM 回调（默认 True）"""
        try:
            from app.service.workflow_service import workflow_service
            wf = await workflow_service.get_workflow(workflow_id)
            trigger_dict = wf.trigger or {}
            return trigger_dict.get("im_callback", True)
        except Exception:
            return True  # 查询失败时默认启用（不阻断回调）

    async def _get_workflow_name(self, workflow_id: str) -> str:
        """获取工作流名称（失败时回退 ID）"""
        try:
            from app.service.workflow_service import workflow_service
            wf = await workflow_service.get_workflow(workflow_id)
            return wf.name or workflow_id
        except Exception:
            return workflow_id

    @staticmethod
    def _format_workflow_message(
        wf_name: str, status: str, outputs: dict, inputs: dict,
    ) -> str:
        """格式化工作流结果为 IM Markdown 消息"""
        if status == "failed":
            return (
                f"❌ **工作流执行失败**\n\n"
                f"**工作流：** {wf_name}\n"
                f"**触发消息：** {(inputs.get('message') or '')[:100]}\n"
                f"请前往控制台查看详细日志。"
            )

        if not outputs:
            return f"✅ **工作流「{wf_name}」已执行完成**"

        lines = [f"✅ **工作流「{wf_name}」执行完成**\n", "**执行结果：**"]
        for key, value in outputs.items():
            if key.startswith("__"):
                continue
            lines.append(f"- `{key}`: {_summarize_value(value)}")
        msg = "\n".join(lines)
        if len(msg) > _MAX_MSG_LEN:
            msg = msg[:_MAX_MSG_LEN - 20] + "\n…（结果已截断）"
        return msg

    # ============ Sprint 6.4: 定时任务结果推送 ============

    async def push_agent_result(
        self,
        task_id: str,
        channel: str,
        chat_id: str,
        trigger_message: str = "",
        timeout: int = _AGENT_WAIT_TIMEOUT,
    ) -> bool:
        """等待 Agent 任务完成 + 推送结果到 IM 会话"""
        if not chat_id or not channel or channel == "api":
            return False

        result = await self._wait_for_agent_result(task_id, timeout)
        if result is None:
            log.warning("IM push skip: agent task {} not found or timeout", task_id)
            return False

        message = self._format_agent_message(trigger_message, result)
        return await self._send_to_im(channel, chat_id, message)

    async def push_notification(
        self, channel: str, chat_id: str, text: str,
    ) -> bool:
        """即时推送通知到 IM（不等待 Agent 结果）"""
        if not chat_id or not channel or channel == "api":
            return False
        return await self._send_to_im(channel, chat_id, text)

    async def _wait_for_agent_result(
        self, task_id: str, timeout: int,
    ) -> Optional[dict]:
        """轮询 Agent 任务状态直到终态或超时"""
        from app.service.agent_persister import get_agent_task
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                task = await get_agent_task(task_id)
                status = task.get("status", "")
                if status in ("completed", "failed", "cancelled"):
                    return task
                await asyncio.sleep(1.0)
            log.warning("Agent task {} wait timeout ({}s)", task_id, timeout)
            return None
        except Exception as e:
            log.warning("Wait agent task {} failed: {}", task_id, e)
            return None

    @staticmethod
    def _format_agent_message(trigger_message: str, task: dict) -> str:
        """格式化 Agent 任务结果为 IM Markdown 消息"""
        status = task.get("status", "unknown")
        reply = task.get("reply") or task.get("final_answer") or ""
        task_id = task.get("task_id", "")
        title = trigger_message[:60] if trigger_message else "定时任务"

        if status == "failed":
            error = task.get("error") or "执行失败"
            return (
                f"❌ **定时任务执行失败**\n\n"
                f"**任务：** {title}\n"
                f"**错误：** {error}\n"
                f"任务ID：`{task_id[:8]}`"
            )
        if status == "cancelled":
            return f"⚠️ **定时任务已取消**\n\n任务：{title}"
        if not reply:
            return f"✅ **定时任务已完成**\n\n**任务：** {title}"

        msg = f"✅ **定时任务结果**\n\n**任务：** {title}\n\n**结果：**\n{reply}"
        if len(msg) > _MAX_MSG_LEN:
            msg = msg[:_MAX_MSG_LEN - 20] + "\n\n…（结果已截断）"
        return msg

    # ============ 共享：渠道发送 ============

    async def _send_to_im(
        self, channel: str, chat_id: str, message: str,
    ) -> bool:
        """通过 ChannelService 发送到 IM"""
        from app.domain.channel.models import Channel
        from app.service.channel_service import channel_service
        try:
            channel_enum = Channel(channel)
        except ValueError:
            log.warning("IM push skip: unknown channel {}", channel)
            return False

        try:
            result = await channel_service.send_text(
                channel_enum, chat_id, message, markdown=True,
            )
            if result.success:
                log.info("IM push sent to {} (channel={})", chat_id, channel)
                return True
            log.warning("IM push failed: {}", result.error)
            return False
        except Exception as e:
            log.warning("IM push error: {}", e)
            return False


def _summarize_value(value, max_len: int = 200) -> str:
    """将 outputs 值格式化为 IM 友好的短字符串"""
    if value is None:
        return "null"
    if isinstance(value, str):
        return value[:max_len] + ("…" if len(value) > max_len else "")
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
        return s[:max_len] + ("…" if len(s) > max_len else "")
    except Exception:
        return str(value)[:max_len]


# 全局单例
im_push_service = IMPushService()
