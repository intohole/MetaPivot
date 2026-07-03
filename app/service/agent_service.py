"""AgentService - 超级 Agent 任务管理

职责：
1. 启动 Agent 任务（异步执行 + 立即返回 task_id）
2. 查询任务状态与步骤
3. SSE 流式推送步骤事件
4. HITL 确认/拒绝/修改
5. 取消任务

依赖：domain/agent（状态机 + 流管理）+ infra/llm + data/orm
持久化逻辑委托给 agent_persister helper，保持本类聚焦业务编排。
"""
import asyncio
from typing import AsyncGenerator

from sqlalchemy import func, select

from app.domain.agent.graph import resume_agent, run_agent
from app.domain.agent.state import AgentState, AgentStatus
from app.domain.agent.stream import stream_manager
from app.infra.db.models_core import AgentTaskORM, AgentTaskStepORM
from app.infra.db.session import get_db_session
from app.service.agent_persister import (
    persist_state,
    persist_steps,
    rebuild_state,
    step_dict,
    update_task_status,
)
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("agent_service")


class AgentService:
    """Agent 服务单例"""

    # 运行中任务引用（避免被GC）
    _running_tasks: dict[str, asyncio.Task] = {}

    # ============ 任务生命周期 ============

    async def start_task(
        self,
        message: str,
        channel: str,
        chat_id: str,
        user_id: str,
        context: dict,
        stream: bool = False,
    ) -> dict:
        """启动 Agent 任务，立即返回 task_id（异步执行，不阻塞）

        stream 参数保留用于 API 兼容（SSE 通过 stream_task 订阅）。
        """
        _ = stream  # API 兼容参数
        async with get_db_session() as session:
            task_orm = AgentTaskORM(
                user_id=user_id or None,
                channel=channel,
                chat_id=chat_id or None,
                original_message=message,
                status="pending",
            )
            session.add(task_orm)
            await session.flush()
            task_id = task_orm.id

        bg = asyncio.create_task(self._run_task(task_id, message, channel, chat_id, user_id, context))
        self._running_tasks[task_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(task_id, None))

        return {
            "task_id": task_id,
            "status": "pending",
            "stream_url": f"/api/v1/agent/tasks/{task_id}/stream",
        }

    async def _run_task(
        self, task_id: str, message: str, channel: str,
        chat_id: str, user_id: str, context: dict,
    ) -> None:
        """后台执行 Agent 状态机"""
        state = AgentState(
            task_id=task_id, user_id=user_id, channel=channel, chat_id=chat_id,
            original_message=message, context=context, max_steps=settings.llm_max_steps,
        )
        await persist_state(task_id, state)

        try:
            async for event in run_agent(state):
                await stream_manager.publish(task_id, event)
                await persist_state(task_id, state)
            await persist_steps(task_id, state)
            await update_task_status(task_id, state.status.value, state.result, state.error)
        except Exception as e:
            log.exception("Agent task {} crashed: {}", task_id, e)
            state.status = AgentStatus.FAILED
            state.error = {"code": "AGENT_ERROR", "message": str(e)}
            await stream_manager.publish(task_id, {"type": "error", "data": state.error})
            await update_task_status(task_id, "failed", None, state.error)
        finally:
            stream_manager.mark_finished(task_id)
            # 延迟清理（保留历史事件5分钟供后续订阅）
            asyncio.get_running_loop().call_later(300, stream_manager.cleanup, task_id)

    # ============ 查询 ============

    async def list_tasks(
        self, page: int = 1, page_size: int = 20,
        user_id: str = "", status: str = "",
    ) -> tuple[list[dict], int]:
        """查询任务列表（user_id 为空时返回全部，admin 场景）"""
        async with get_db_session() as session:
            stmt = select(AgentTaskORM)
            if user_id:
                stmt = stmt.where(AgentTaskORM.user_id == user_id)
            if status:
                stmt = stmt.where(AgentTaskORM.status == status)
            total = (await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            stmt = (stmt.order_by(AgentTaskORM.created_at.desc())
                    .offset((page - 1) * page_size).limit(page_size))
            items = (await session.execute(stmt)).scalars().all()
            return [self._task_summary(t) for t in items], total

    @staticmethod
    def _task_summary(t: AgentTaskORM) -> dict:
        """任务列表项摘要（不含 steps，减少传输量）"""
        return {
            "task_id": t.id, "status": t.status, "channel": t.channel,
            "user_id": t.user_id, "result": t.result,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }

    async def get_task(self, task_id: str, user_id: str = "") -> dict:
        async with get_db_session() as session:
            task = await session.get(AgentTaskORM, task_id)
            if task is None:
                raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
            # 越权防护：仅任务发起人可查询（admin 由路由层 require_permission 放行）
            if user_id and task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "无权访问该任务", 403)
            steps = (await session.execute(
                select(AgentTaskStepORM)
                .where(AgentTaskStepORM.task_id == task_id)
                .order_by(AgentTaskStepORM.step_index)
            )).scalars().all()
            return {
                "task_id": task.id,
                "status": task.status,
                "result": task.result,
                "steps": [step_dict(s) for s in steps],
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "error": task.error,
            }

    async def stream_task(self, task_id: str, user_id: str = "") -> AsyncGenerator[dict, None]:
        """SSE 订阅任务事件流"""
        async with get_db_session() as session:
            task = await session.get(AgentTaskORM, task_id)
            if task is None:
                raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
            if user_id and task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "无权访问该任务", 403)

        queue = stream_manager.subscribe(task_id)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("type") == "stream_end":
                    return
        finally:
            stream_manager.unsubscribe(task_id, queue)

    # ============ HITL ============

    async def confirm_task(
        self, task_id: str, decision: str, modifications: dict, user_id: str,
    ) -> dict:
        """HITL 确认/拒绝/修改"""
        if decision not in ("approve", "reject", "modify"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "decision 必须为 approve/reject/modify", 400)
        if decision == "modify" and not modifications:
            raise AppError(ErrorCode.VALIDATION_ERROR, "modify 时 modifications 必填", 400)

        async with get_db_session() as session:
            task = await session.get(AgentTaskORM, task_id)
            if task is None:
                raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
            if task.status != "waiting_confirm":
                raise AppError(ErrorCode.VALIDATION_ERROR, f"任务当前状态 {task.status} 不可确认", 400)
            # 权限校验：仅任务发起人可确认（admin 由路由层 require_permission 保证）
            if task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "仅任务发起人可确认", 403)

        state = rebuild_state(task, settings.llm_max_steps)
        state.confirm_decision = decision
        state.confirm_modifications = modifications or None

        bg = asyncio.create_task(self._resume_task(task_id, state))
        self._running_tasks[task_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(task_id, None))
        return {"task_id": task_id, "status": "executing"}

    async def _resume_task(self, task_id: str, state: AgentState) -> None:
        """恢复 HITL 暂停的任务"""
        try:
            async for event in resume_agent(state):
                await stream_manager.publish(task_id, event)
                await persist_state(task_id, state)
            await persist_steps(task_id, state)
            await update_task_status(task_id, state.status.value, state.result, state.error)
        except Exception as e:
            log.exception("Agent resume {} failed: {}", task_id, e)
            await update_task_status(task_id, "failed", None, {"message": str(e)})
        finally:
            stream_manager.mark_finished(task_id)

    # ============ 取消 ============

    async def cancel_task(self, task_id: str, user_id: str) -> dict:
        async with get_db_session() as session:
            task = await session.get(AgentTaskORM, task_id)
            if task is None:
                raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
            if task.status in ("completed", "failed", "cancelled"):
                raise AppError(ErrorCode.AGENT_TASK_CANCELLED, "任务已结束", 400)
            if task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "仅任务发起人可取消", 403)
            task.status = "cancelled"
        bg = self._running_tasks.pop(task_id, None)
        if bg and not bg.done():
            bg.cancel()
        await stream_manager.publish(task_id, {"type": "cancelled", "data": {}})
        stream_manager.mark_finished(task_id)
        return {"task_id": task_id, "status": "cancelled"}


agent_service = AgentService()
