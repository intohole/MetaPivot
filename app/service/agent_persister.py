"""Agent 任务持久化 Helper

职责：AgentState ↔ ORM 的转换与落库，与业务编排解耦。
被 AgentService 调用，依赖 domain/agent + data/orm，依赖方向向下。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update

from app.domain.agent.state import AgentState, AgentStatus
from app.infra.db.models_core import AgentTaskORM, AgentTaskStepORM
from app.infra.db.session import get_db_session


async def persist_state(task_id: str, state: AgentState) -> None:
    """更新任务主表状态（含 Token 用量累计）"""
    async with get_db_session() as session:
        await session.execute(
            update(AgentTaskORM)
            .where(AgentTaskORM.id == task_id)
            .values(
                status=state.status.value,
                intent=state.intent,
                current_step=state.current_step,
                plan=state.plan,
                result=state.result,
                error=state.error,
                total_tokens=state.total_tokens,
                updated_at=datetime.now(),
            )
        )


async def persist_steps(task_id: str, state: AgentState) -> None:
    """持久化步骤记录（仅新增，幂等；含 token_usage / llm_duration_ms / tool_duration_ms）"""
    if not state.steps:
        return
    async with get_db_session() as session:
        existing = (await session.execute(
            select(AgentTaskStepORM.step_index)
            .where(AgentTaskStepORM.task_id == task_id)
        )).scalars().all()
        for step in state.steps:
            if step.step_index in existing:
                continue
            session.add(AgentTaskStepORM(
                task_id=task_id,
                step_index=step.step_index,
                step_name=step.step_name,
                tool_name=step.tool_name,
                tool_input=step.tool_input,
                tool_output=step.tool_output,
                require_confirm=step.require_confirm,
                confirm_decision=step.confirm_decision,
                confirm_user=step.confirm_user,
                status=step.status,
                duration_ms=step.duration_ms,
                llm_duration_ms=step.llm_duration_ms,
                tool_duration_ms=step.tool_duration_ms,
                token_usage=step.token_usage,
                error=step.error,
            ))


def rebuild_state(task: AgentTaskORM, max_steps: int) -> AgentState:
    """从 DB 重建 AgentState（用于恢复 HITL）"""
    from app.utils.config import settings  # 局部导入避免循环
    return AgentState(
        task_id=task.id,
        user_id=task.user_id or "",
        channel=task.channel,
        chat_id=task.chat_id or "",
        original_message=task.original_message or "",
        intent=task.intent or "",
        mode=task.mode or "agent",
        status=AgentStatus(task.status),
        current_step=task.current_step,
        plan=task.plan or [],
        max_steps=max_steps or settings.llm_max_steps,
        result=task.result or {},
        error=task.error,
        total_tokens=task.total_tokens or 0,
    )


async def update_task_status(
    task_id: str,
    status: str,
    result: Optional[dict],
    error: Optional[dict],
    total_tokens: Optional[int] = None,
    started_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """更新任务终态（含 Token 用量 + 执行耗时，用于成本/性能分析）"""
    now = datetime.now()
    values: dict = {"status": status, "result": result, "error": error, "updated_at": now}
    if total_tokens is not None:
        values["total_tokens"] = total_tokens
    if started_at is not None:
        values["started_at"] = started_at
    # 终态写入 finished_at + duration_ms（completed/failed/cancelled）
    if status in ("completed", "failed", "cancelled"):
        values["finished_at"] = now
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        elif started_at is not None:
            values["duration_ms"] = int((now - started_at).total_seconds() * 1000)
    async with get_db_session() as session:
        await session.execute(
            update(AgentTaskORM).where(AgentTaskORM.id == task_id).values(**values)
        )


def _task_summary(t: AgentTaskORM) -> dict:
    """任务列表项摘要（不含 steps，减少传输量）"""
    return {
        "task_id": t.id, "status": t.status, "channel": t.channel,
        "user_id": t.user_id, "result": t.result,
        "total_tokens": t.total_tokens,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


async def list_agent_tasks(
    page: int = 1, page_size: int = 20, user_id: str = "", status: str = "",
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
        return [_task_summary(t) for t in items], total


async def get_agent_task(task_id: str, user_id: str = "") -> dict:
    """查询任务详情（含 steps；越权防护：仅任务发起人，admin 由路由层放行）"""
    async with get_db_session() as session:
        task = await session.get(AgentTaskORM, task_id)
        if task is None:
            from app.utils.response import AppError, ErrorCode
            raise AppError(ErrorCode.AGENT_TASK_NOT_FOUND, status_code=404)
        if user_id and task.user_id and task.user_id != user_id:
            from app.utils.response import AppError, ErrorCode
            raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "无权访问该任务", 403)
        steps = (await session.execute(
            select(AgentTaskStepORM)
            .where(AgentTaskStepORM.task_id == task_id)
            .order_by(AgentTaskStepORM.step_index)
        )).scalars().all()
        return {
            "task_id": task.id, "status": task.status, "result": task.result,
            "total_tokens": task.total_tokens,
            "steps": [step_dict(s) for s in steps],
            "error": task.error,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "duration_ms": task.duration_ms,
        }


def step_dict(s: AgentTaskStepORM) -> dict:
    """ORM step → dict"""
    return {
        "step_index": s.step_index,
        "step_name": s.step_name,
        "tool_name": s.tool_name,
        "tool_input": s.tool_input,
        "tool_output": s.tool_output,
        "require_confirm": s.require_confirm,
        "confirm_decision": s.confirm_decision,
        "status": s.status,
        "duration_ms": s.duration_ms,
        "llm_duration_ms": s.llm_duration_ms,
        "tool_duration_ms": s.tool_duration_ms,
        "token_usage": s.token_usage,
        "error": s.error,
    }


async def audit_task_result(
    task_id: str, user_id: str, channel: str, message: str,
    state: AgentState, started_at: Optional[datetime], request_id: str,
) -> None:
    """审计 Agent 任务执行结果（非阻塞，失败不影响主流程）

    被 AgentService._run_task / _resume_task 在 finally 块调用，
    记录任务的输入摘要、输出摘要、耗时、成功/失败状态，用于成本与性能追踪。
    """
    from app.service.audit_service import audit_service
    try:
        duration = int((datetime.now() - started_at).total_seconds() * 1000) if started_at else None
        await audit_service.log_action(
            user_id=user_id, action="agent.task", task_id=task_id,
            input_data={"message": message, "channel": channel},
            output_data=state.result, duration_ms=duration,
            status="success" if state.status == AgentStatus.COMPLETED else "failed",
            error_message=state.error.get("message") if state.error else None,
            request_id=request_id,
        )
    except Exception as e:
        from app.utils.logger import get_logger
        get_logger("agent_persister").warning("audit agent task {} failed: {}", task_id, e)


async def audit_task_event(
    task_id: str, user_id: str, action: str,
    input_data: dict, status: str = "success", request_id: str = "",
) -> None:
    """审计 Agent 任务事件（cancel/confirm 等，非阻塞）"""
    from app.service.audit_service import audit_service
    try:
        await audit_service.log_action(
            user_id=user_id, action=action, task_id=task_id,
            input_data=input_data, status=status, request_id=request_id,
        )
    except Exception as e:
        from app.utils.logger import get_logger
        get_logger("agent_persister").warning("audit {} failed: {}", action, e)
