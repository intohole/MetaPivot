"""Agent 任务执行器 — 后台执行 + 状态机消费 + HITL 恢复

Sprint 7.4: 从 agent_service.py 拆离，保持 agent_service.py ≤ 300 行。
职责：
- run_task: 后台执行 Agent 状态机（加载历史 → RAG → 摘要 → 创建 state → 消费事件）
- consume_agent: Agent 主循环消费者（被 wait_for 包裹，异常写 FAILED）
- resume_task: 恢复 HITL 暂停的任务
- _trigger_skill_evolution: 任务完成后异步触发 Skill 自进化

设计：模块级函数，接受 svc（AgentService 实例）以访问 DI 注入的依赖。
"""
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from app.domain.agent.graph import resume_agent, run_agent
from app.domain.agent.state import AgentState, AgentStatus
from app.domain.agent.stream import stream_manager
from app.service.agent_persister import (
    audit_task_result,
    persist_event,
    persist_state,
    persist_steps,
    update_task_status,
)
from app.utils.config import settings
from app.utils.context import get_request_id, get_trace_id, set_request_context
from app.utils.logger import get_logger
from app.utils.metrics import agent_task_finished, record_agent_task

if TYPE_CHECKING:
    from app.service.agent_service import AgentService

log = get_logger("agent_runner")


async def _trigger_skill_evolution(task_id: str, status: str) -> None:
    """Skill 自进化后台触发器（任务完成后异步执行，不阻塞响应）

    - completed → 经验固化（try_solidify_experience）
    - failed → 失败分析（analyze_failure）
    异常隔离：任何错误只记日志，不影响主流程。
    """
    try:
        if status == "completed":
            from app.domain.skill.evolution import try_solidify_experience
            result = await try_solidify_experience(task_id)
            if result.get("solidified"):
                log.info("Skill evolution: solidified task {} → draft {}", task_id, result.get("draft_id"))
        elif status == "failed":
            from app.domain.skill.failure_analyzer import analyze_failure
            result = await analyze_failure(task_id)
            if result.get("worth_sediment"):
                log.info("Skill evolution: failure analyzed task {} → draft {}", task_id, result.get("draft_id"))
    except Exception as e:
        log.warning("Skill evolution trigger failed for task {}: {}", task_id, e)


async def run_task(
    svc: "AgentService",
    task_id: str,
    message: str,
    channel: str,
    chat_id: str,
    user_id: str,
    context: dict,
    tenant_id: str = "default",
) -> None:
    """后台执行 Agent 状态机

    Phase 1: 用 asyncio.wait_for 包裹 _consume_agent，实现任务级超时
    （settings.agent_task_timeout，默认 300s），超时写 FAILED + persist_state。
    """
    from app.service.agent_rag import build_rag_context

    request_id = get_request_id() or f"task-{uuid4().hex[:8]}"
    if not get_request_id():
        set_request_context(request_id, user_id=user_id or "")

    started_at = datetime.now()
    # 加载多轮对话历史（跨任务记忆，企业办公"刚才那个"场景）
    history_messages: list[dict] = []
    if chat_id and svc._memory_store is not None:
        try:
            history_messages = await svc._memory_store.load_history(chat_id, limit=20)
            rag_ctx = await build_rag_context(
                message, chat_id, user_id,
                svc._memory_store, svc._retriever, svc._query_router,
            )
            if rag_ctx:
                history_messages = [{"role": "system", "content": rag_ctx}] + history_messages
        except Exception as e:
            log.warning("load_history failed for {}: {}", chat_id, e)

    # Phase B4: Context Summarization - 长对话压缩（len>=20 触发）
    if history_messages and len(history_messages) >= 20 and svc._memory_store and chat_id:
        try:
            existing_summary = await svc._memory_store.get_summary(chat_id)
            if not existing_summary:
                from app.domain.agent.context_window import summarize_messages
                from app.infra.llm.provider import get_llm
                summary = await summarize_messages(
                    history_messages, get_llm(),
                    memory_store=svc._memory_store, chat_id=chat_id,
                )
                if summary:
                    log.info("Context summarized for {}: {} chars", chat_id, len(summary))
        except Exception as e:
            log.warning("Context summarization failed for {}: {}", chat_id, e)

    state = AgentState(
        task_id=task_id, user_id=user_id, channel=channel, chat_id=chat_id,
        original_message=message, context=context, max_steps=settings.llm_max_steps,
        messages=list(history_messages), started_at=started_at,
        request_id=request_id, trace_id=get_trace_id() or request_id,
        tenant_id=tenant_id,
    )
    await persist_state(task_id, state)

    runner = asyncio.create_task(
        consume_agent(svc, task_id, state, started_at, request_id, is_resume=False)
    )
    try:
        await asyncio.wait_for(runner, timeout=settings.agent_task_timeout)
    except asyncio.TimeoutError:
        runner.cancel()
        state.status = AgentStatus.FAILED
        state.error = {"code": "TASK_TIMEOUT", "message": f"任务超过 {settings.agent_task_timeout}s"}
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
        await audit_task_result(task_id, user_id, channel, message, state, started_at, request_id, tenant_id=tenant_id)
        asyncio.get_running_loop().call_later(300, stream_manager.cleanup, task_id)
        # Skill 自进化：任务完成后异步触发经验固化/失败分析
        asyncio.get_running_loop().create_task(_trigger_skill_evolution(task_id, state.status.value))


async def consume_agent(
    svc: "AgentService",
    task_id: str,
    state: AgentState,
    started_at: datetime,
    request_id: str,
    is_resume: bool = False,
) -> None:
    """Agent 主循环消费者（被 run_task/resume_task 用 wait_for 包裹）

    is_resume=True 时调用 resume_agent（HITL 恢复），否则调用 run_agent。
    异常在此捕获并写 FAILED，外层 wait_for 不会 raise（除非超时）。
    """
    from app.infra.observability.baggage import attach_user_baggage, detach_user_baggage
    from app.service.agent_rag import persist_to_memory

    baggage_token = attach_user_baggage(state.user_id, state.chat_id, task_id)
    runner_fn = resume_agent if is_resume else run_agent
    try:
        async for event in runner_fn(state):
            await stream_manager.publish(task_id, event)
            await persist_state(task_id, state)
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
            await persist_to_memory(
                svc._memory_store, state.chat_id,
                state.original_message, state.final_answer,
            )
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
            await persist_to_memory(svc._memory_store, state.chat_id, state.original_message, "")
    finally:
        detach_user_baggage(baggage_token)


async def resume_task(svc: "AgentService", task_id: str, state: AgentState, tenant_id: str = "default") -> None:
    """恢复 HITL 暂停的任务（与 run_task 对称：wait_for 包裹 consume_agent）"""
    request_id = get_request_id() or f"resume-{uuid4().hex[:8]}"
    if not get_request_id():
        set_request_context(request_id, user_id=state.user_id or "")

    started_at = state.started_at or datetime.now()
    runner = asyncio.create_task(
        consume_agent(svc, task_id, state, started_at, request_id, is_resume=True)
    )
    try:
        await asyncio.wait_for(runner, timeout=settings.agent_task_timeout)
    except asyncio.TimeoutError:
        runner.cancel()
        state.status = AgentStatus.FAILED
        state.error = {"code": "TASK_TIMEOUT", "message": f"任务超过 {settings.agent_task_timeout}s"}
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
        await audit_task_result(task_id, state.user_id, state.channel, state.original_message, state, started_at, request_id, tenant_id=tenant_id)
        # Skill 自进化：HITL 恢复完成后异步触发（与 run_task 对称）
        asyncio.get_running_loop().create_task(_trigger_skill_evolution(task_id, state.status.value))
