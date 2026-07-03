"""Agent 任务持久化 Helper

职责：AgentState ↔ ORM 的转换与落库，与业务编排解耦。
被 AgentService 调用，依赖 domain/agent + data/orm，依赖方向向下。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from app.domain.agent.state import AgentState, AgentStatus
from app.infra.db.models_core import AgentTaskORM, AgentTaskStepORM
from app.infra.db.session import get_db_session


async def persist_state(task_id: str, state: AgentState) -> None:
    """更新任务主表状态"""
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
                updated_at=datetime.now(),
            )
        )


async def persist_steps(task_id: str, state: AgentState) -> None:
    """持久化步骤记录（仅新增，幂等）"""
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
    )


async def update_task_status(
    task_id: str,
    status: str,
    result: Optional[dict],
    error: Optional[dict],
) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(AgentTaskORM)
            .where(AgentTaskORM.id == task_id)
            .values(status=status, result=result, error=error, updated_at=datetime.now())
        )


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
        "error": s.error,
    }
