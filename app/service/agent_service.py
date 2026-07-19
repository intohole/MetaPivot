"""AgentService - 超级 Agent 任务管理（公共 API 层）

职责：
1. 启动 Agent 任务（异步执行 + 立即返回 task_id）
2. 查询任务状态与步骤
3. SSE 流式推送步骤事件
4. HITL 确认/拒绝/修改
5. 取消任务

Sprint 7.4: 拆分执行逻辑到 agent_runner.py，RAG/记忆辅助到 agent_rag.py，
保持本文件 ≤ 300 行，聚焦公共 API + DI 注入。

依赖：domain/agent（状态机 + 流管理）+ infra/llm + data/orm
持久化逻辑委托给 agent_persister helper，执行逻辑委托给 agent_runner。
"""
import asyncio
from datetime import datetime
from typing import AsyncGenerator, Optional
from uuid import uuid4

from app.domain.agent.stream import stream_manager
from app.domain.contracts.judge import IJudge
from app.domain.contracts.memory import IMemoryStore
from app.domain.contracts.retrieval import IQueryRouter, IRetriever
from app.domain.contracts.verifier import IVerifier
from app.infra.db.models_agent import AgentTaskORM
from app.infra.db.session import get_db_session
from app.service.agent_persister import (
    audit_task_event,
    get_agent_task,
    list_agent_tasks,
    rebuild_state,
)
from app.utils.config import settings
from app.utils.context import get_request_id, get_trace_id, set_request_context
from app.utils.logger import get_logger
from app.utils.metrics import agent_task_finished, agent_task_started, record_agent_task
from app.utils.response import AppError, ErrorCode

log = get_logger("agent_service")


class AgentService:
    """Agent 服务单例 — 公共 API + DI 注入口"""

    # 运行中任务引用（避免被GC）
    _running_tasks: dict[str, asyncio.Task] = {}
    # 多轮对话记忆存储（DI 注入，避免 Domain 层直接依赖 Infra）
    _memory_store: Optional[IMemoryStore] = None
    # L4 Judge 评估器（DI 注入，保留扩展点）
    _judge: Optional[IJudge] = None
    # Phase 4.1: Agentic RAG 三库统一检索（DI 注入）
    _retriever: Optional[IRetriever] = None
    _query_router: Optional[IQueryRouter] = None
    # Phase 4.2: 结果验证器（DI 注入，保留扩展点）
    _verifier: Optional[IVerifier] = None

    # ============ DI 注入 ============

    def set_memory_store(self, store: IMemoryStore) -> None:
        """DI 注入记忆存储（由 main.py lifespan 调用）

        避免在 Domain/Service 层直接 import Infra 层的 memory factory，
        保持分层依赖方向：route→service→domain→contracts（不向下到 infra）。
        """
        self._memory_store = store
        log.info("MemoryStore injected into AgentService")

    def set_judge(self, judge: IJudge) -> None:
        """DI 注入 Judge 评估器（保留扩展点，当前 graph.py 直接用 get_judge 单例）"""
        self._judge = judge
        log.info("Judge injected into AgentService")

    def set_retriever(self, retriever: IRetriever) -> None:
        """Phase 4.1: DI 注入三库统一检索器（注入后走 Adaptive RAG）"""
        self._retriever = retriever
        log.info("Retriever injected into AgentService")

    def set_query_router(self, router: IQueryRouter) -> None:
        """Phase 4.1: DI 注入查询路由器"""
        self._query_router = router
        log.info("QueryRouter injected into AgentService")

    def set_verifier(self, verifier: IVerifier) -> None:
        """Phase 4.2: DI 注入结果验证器（保留扩展点，当前 graph.py 直接用 get_verifier 单例）"""
        self._verifier = verifier
        log.info("Verifier injected into AgentService")

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
        """启动 Agent 任务，立即返回 task_id（异步执行，不阻塞）"""
        _ = stream  # API 兼容参数
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

        # Sprint 7.4: 执行逻辑委托给 agent_runner
        from app.service.agent_runner import run_task
        bg = asyncio.create_task(run_task(self, task_id, message, channel, chat_id, user_id, context))
        self._running_tasks[task_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(task_id, None))

        agent_task_started()
        return {
            "task_id": task_id,
            "status": "pending",
            "stream_url": f"/api/v1/agent/tasks/{task_id}/stream",
        }

    async def list_tasks(
        self, page: int = 1, page_size: int = 20,
        user_id: str = "", status: str = "",
    ) -> tuple[list[dict], int]:
        """查询任务列表（user_id 为空时返回全部，admin 场景）"""
        return await list_agent_tasks(page, page_size, user_id, status)

    async def start_task_and_wait(
        self,
        message: str,
        channel: str = "workflow",
        chat_id: str = "",
        user_id: str = "",
        context: dict = None,
        timeout: int = 120,
    ) -> dict:
        """启动 Agent 任务并同步等待结果（工作流 agent_call 节点用）"""
        result = await self.start_task(
            message=message, channel=channel, chat_id=chat_id,
            user_id=user_id, context=context or {},
        )
        task_id = result["task_id"]
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            task = await get_agent_task(task_id)
            status = task.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                return task
            await asyncio.sleep(0.5)
        raise asyncio.TimeoutError(f"Agent task {task_id} timeout ({timeout}s)")

    async def search_memory(
        self, query: str, chat_id: str = "", top_k: int = 5,
    ) -> list[dict]:
        """语义记忆检索（委托给 agent_rag）"""
        from app.service.agent_rag import search_memory
        return await search_memory(self._memory_store, query, chat_id, top_k)

    async def get_task(self, task_id: str, user_id: str = "") -> dict:
        """查询任务详情（含 steps；越权防护：仅任务发起人）"""
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
            if task.user_id and task.user_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "仅任务发起人可确认", 403)

        state = rebuild_state(task, settings.llm_max_steps)
        state.confirm_decision = decision
        state.confirm_modifications = modifications or None

        # Sprint 7.4: 恢复逻辑委托给 agent_runner
        from app.service.agent_runner import resume_task
        bg = asyncio.create_task(resume_task(self, task_id, state))
        self._running_tasks[task_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(task_id, None))
        await audit_task_event(
            task_id, user_id, "agent.task.confirm",
            {"decision": decision, "modifications": modifications}, request_id=get_request_id(),
        )
        return {"task_id": task_id, "status": "executing"}

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
        await audit_task_event(
            task_id, user_id, "agent.task.cancel",
            {"message": task.original_message}, status="cancelled", request_id=get_request_id(),
        )
        return {"task_id": task_id, "status": "cancelled"}


agent_service = AgentService()
