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
from datetime import datetime
from typing import AsyncGenerator, Optional
from uuid import uuid4

from app.domain.agent.graph import resume_agent, run_agent
from app.domain.agent.state import AgentState, AgentStatus
from app.domain.agent.stream import stream_manager
from app.domain.contracts.judge import IJudge
from app.domain.contracts.memory import IMemoryStore
from app.infra.db.models_core import AgentTaskORM
from app.infra.db.session import get_db_session
from app.service.agent_persister import (
    audit_task_event,
    audit_task_result,
    get_agent_task,
    list_agent_tasks,
    persist_state,
    persist_steps,
    rebuild_state,
    update_task_status,
)
from app.utils.config import settings
from app.utils.context import get_request_id, get_trace_id, set_request_context
from app.utils.logger import get_logger
from app.utils.metrics import agent_task_finished, record_agent_task
from app.utils.response import AppError, ErrorCode

log = get_logger("agent_service")


class AgentService:
    """Agent 服务单例"""

    # 运行中任务引用（避免被GC）
    _running_tasks: dict[str, asyncio.Task] = {}
    # 多轮对话记忆存储（DI 注入，避免 Domain 层直接依赖 Infra）
    _memory_store: Optional[IMemoryStore] = None
    # L4 Judge 评估器（DI 注入，保留扩展点；当前 graph.py 直接用 get_judge 单例）
    _judge: Optional[IJudge] = None

    def set_memory_store(self, store: IMemoryStore) -> None:
        """DI 注入记忆存储（由 main.py lifespan 调用）

        避免在 Domain/Service 层直接 import Infra 层的 memory factory，
        保持分层依赖方向：route→service→domain→contracts（不向下到 infra）。
        """
        self._memory_store = store
        log.info("MemoryStore injected into AgentService")

    def set_judge(self, judge: IJudge) -> None:
        """DI 注入 Judge 评估器（由 main.py lifespan 调用）

        保留扩展点：当前 graph.py 直接 from app.domain.agent.judge import get_judge
        调用单例；后续若需多 backend（如规则 Judge / 远程 Judge 服务），
        可通过此方法注入自定义 IJudge 实现。
        """
        self._judge = judge
        log.info("Judge injected into AgentService")

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
        # 生成 request_id/trace_id（用于跨任务日志关联 + Phase 4 OTel Baggage 传播）
        rid = get_request_id() or f"task-{uuid4().hex[:8]}"
        tid = get_trace_id() or rid
        if not get_request_id():
            set_request_context(rid, user_id=user_id or "")
        async with get_db_session() as session:
            task_orm = AgentTaskORM(
                user_id=user_id or None,
                channel=channel,
                chat_id=chat_id or None,
                original_message=message,
                status="pending",
                request_id=rid,
                trace_id=tid,
            )
            session.add(task_orm)
            await session.flush()
            task_id = task_orm.id

        bg = asyncio.create_task(self._run_task(task_id, message, channel, chat_id, user_id, context))
        self._running_tasks[task_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(task_id, None))

        from app.utils.metrics import agent_task_started
        agent_task_started()
        return {
            "task_id": task_id,
            "status": "pending",
            "stream_url": f"/api/v1/agent/tasks/{task_id}/stream",
        }

    async def _run_task(
        self, task_id: str, message: str, channel: str,
        chat_id: str, user_id: str, context: dict,
    ) -> None:
        """后台执行 Agent 状态机

        Phase 1: 用 asyncio.wait_for 包裹 _consume_agent，实现任务级超时
        （settings.agent_task_timeout，默认 300s），超时写 FAILED + persist_state。
        """
        request_id = get_request_id() or f"task-{uuid4().hex[:8]}"
        if not get_request_id():
            set_request_context(request_id, user_id=user_id or "")

        started_at = datetime.now()
        # 加载多轮对话历史（跨任务记忆，企业办公"刚才那个"场景）
        history_messages: list[dict] = []
        if chat_id and self._memory_store is not None:
            try:
                history_messages = await self._memory_store.load_history(chat_id, limit=20)
            except Exception as e:
                log.warning("load_history failed for {}: {}", chat_id, e)

        state = AgentState(
            task_id=task_id, user_id=user_id, channel=channel, chat_id=chat_id,
            original_message=message, context=context, max_steps=settings.llm_max_steps,
            messages=list(history_messages), started_at=started_at,
            request_id=request_id, trace_id=get_trace_id() or request_id,
        )
        await persist_state(task_id, state)

        runner = asyncio.create_task(
            self._consume_agent(task_id, state, started_at, request_id, is_resume=False)
        )
        try:
            await asyncio.wait_for(runner, timeout=settings.agent_task_timeout)
        except asyncio.TimeoutError:
            runner.cancel()
            state.status = AgentStatus.FAILED
            state.error = {
                "code": "TASK_TIMEOUT",
                "message": f"任务超过 {settings.agent_task_timeout}s",
            }
            await persist_state(task_id, state)
            await update_task_status(
                task_id, "failed", None, state.error,
                total_tokens=state.total_tokens, started_at=started_at,
            )
            await stream_manager.publish(task_id, {"type": "error", "data": state.error})
        finally:
            duration = (datetime.now() - started_at).total_seconds() if started_at else None
            stream_manager.mark_finished(task_id)
            agent_task_finished()
            record_agent_task(state.status.value, duration)
            await audit_task_result(task_id, user_id, channel, message, state, started_at, request_id)
            asyncio.get_running_loop().call_later(300, stream_manager.cleanup, task_id)

    async def _consume_agent(
        self,
        task_id: str,
        state: AgentState,
        started_at: datetime,
        request_id: str,
        is_resume: bool = False,
    ) -> None:
        """Agent 主循环消费者（被 _run_task/_resume_task 用 asyncio.wait_for 包裹）

        is_resume=True 时调用 resume_agent（HITL 恢复），否则调用 run_agent。
        异常在此捕获并写 FAILED，外层 wait_for 不会 raise（除非超时）。
        Phase 4: 入口 attach_user_baggage 传播 OTel Baggage；每个事件 fire-and-forget persist_event。
        """
        # Phase 4: Baggage 跨 asyncio.create_task 传播（contextvars 不自动跨 task）
        from app.infra.observability.baggage import attach_user_baggage, detach_user_baggage
        baggage_token = attach_user_baggage(state.user_id, state.chat_id, task_id)

        # Phase 4: persist_event import（fire-and-forget 事件持久化，会话重放用）
        from app.service.agent_persister import persist_event

        runner_fn = resume_agent if is_resume else run_agent
        try:
            async for event in runner_fn(state):
                await stream_manager.publish(task_id, event)
                await persist_state(task_id, state)
                # Phase 4: fire-and-forget 持久化事件（不阻塞主链路）
                ev_type = event.get("type", "")
                ev_data = event.get("data", {})
                asyncio.create_task(persist_event(
                    task_id=task_id, event_type=ev_type, event_data=ev_data,
                    step_index=state.current_step, request_id=request_id,
                ))
            await persist_steps(task_id, state)
            await update_task_status(
                task_id, state.status.value, state.result, state.error,
                total_tokens=state.total_tokens, started_at=started_at,
            )
            if not is_resume:
                # 仅首次执行时持久化到记忆存储（恢复时不重复写用户消息）
                await self._persist_to_memory(state.chat_id, state.original_message, state.final_answer)
        except Exception as e:
            log.exception("Agent task {} crashed: {}", task_id, e)
            state.status = AgentStatus.FAILED
            state.error = {"code": "AGENT_ERROR", "message": str(e)}
            await stream_manager.publish(task_id, {"type": "error", "data": state.error})
            await update_task_status(
                task_id, "failed", None, state.error,
                total_tokens=state.total_tokens, started_at=started_at,
            )
            if not is_resume:
                await self._persist_to_memory(state.chat_id, state.original_message, "")
        finally:
            # Phase 4: Baggage detach（与 attach 配对）
            detach_user_baggage(baggage_token)

    async def _persist_to_memory(
        self, chat_id: str, user_message: str, assistant_answer: str
    ) -> None:
        """持久化本轮对话到记忆存储（user + assistant 消息）"""
        if not chat_id or not self._memory_store or not user_message:
            return
        try:
            await self._memory_store.append_message(chat_id, "user", user_message)
            if assistant_answer:
                await self._memory_store.append_message(chat_id, "assistant", assistant_answer)
        except Exception as e:
            log.warning("persist_to_memory failed for {}: {}", chat_id, e)

    # ============ 查询 ============

    async def list_tasks(
        self, page: int = 1, page_size: int = 20,
        user_id: str = "", status: str = "",
    ) -> tuple[list[dict], int]:
        """查询任务列表（user_id 为空时返回全部，admin 场景）"""
        return await list_agent_tasks(page, page_size, user_id, status)

    async def get_task(self, task_id: str, user_id: str = "") -> dict:
        """查询任务详情（含 steps；越权防护：仅任务发起人，admin 由路由层放行）"""
        return await get_agent_task(task_id, user_id)

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
                # 识别 stream_end 和 cancelled 两种终止事件
                if event.get("type") in ("stream_end", "cancelled"):
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
        from app.utils.context import get_request_id
        await audit_task_event(
            task_id, user_id, "agent.task.confirm",
            {"decision": decision, "modifications": modifications}, request_id=get_request_id(),
        )
        return {"task_id": task_id, "status": "executing"}

    async def _resume_task(self, task_id: str, state: AgentState) -> None:
        """恢复 HITL 暂停的任务（与 _run_task 对称：asyncio.wait_for 包裹 _consume_agent）"""
        request_id = get_request_id() or f"resume-{uuid4().hex[:8]}"
        if not get_request_id():
            set_request_context(request_id, user_id=state.user_id or "")

        started_at = state.started_at or datetime.now()
        runner = asyncio.create_task(
            self._consume_agent(task_id, state, started_at, request_id, is_resume=True)
        )
        try:
            await asyncio.wait_for(runner, timeout=settings.agent_task_timeout)
        except asyncio.TimeoutError:
            runner.cancel()
            state.status = AgentStatus.FAILED
            state.error = {
                "code": "TASK_TIMEOUT",
                "message": f"任务超过 {settings.agent_task_timeout}s",
            }
            await persist_state(task_id, state)
            await update_task_status(
                task_id, "failed", None, state.error,
                total_tokens=state.total_tokens, started_at=started_at,
            )
            await stream_manager.publish(task_id, {"type": "error", "data": state.error})
        finally:
            duration = (datetime.now() - started_at).total_seconds() if started_at else None
            stream_manager.mark_finished(task_id)
            agent_task_finished()
            record_agent_task(state.status.value, duration)
            await audit_task_result(task_id, state.user_id, state.channel, state.original_message, state, started_at, request_id)

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
            if task.started_at:
                task.finished_at = datetime.now()
                task.duration_ms = int((task.finished_at - task.started_at).total_seconds() * 1000)
        bg = self._running_tasks.pop(task_id, None)
        if bg and not bg.done():
            bg.cancel()
        await stream_manager.publish(task_id, {"type": "cancelled", "data": {}})
        stream_manager.mark_finished(task_id)
        agent_task_finished()
        record_agent_task("cancelled", task.duration_ms / 1000 if task.duration_ms else None)
        from app.utils.context import get_request_id
        await audit_task_event(
            task_id, user_id, "agent.task.cancel",
            {"message": task.original_message}, status="cancelled", request_id=get_request_id(),
        )
        return {"task_id": task_id, "status": "cancelled"}


agent_service = AgentService()
