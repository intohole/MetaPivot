"""Workflow 执行器 — DAG 推进 + 状态更新 + IM 回调

Sprint 8.1: 从 workflow_service.py 拆离，保持 workflow_service.py ≤ 300 行。
职责：
- run_execution: 异步推进工作流 DAG（HITL 暂停/恢复/完成/失败）
- update_execution: 更新执行实例状态

设计：模块级函数，接受 svc（WorkflowService 实例）以访问 get_workflow。
参照 agent_runner.py / skill_executor.py 的 svc 委托模式。
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.domain.workflow.engine import WorkflowDefinition, execute_node
from app.infra.db.models_core import WorkflowExecutionORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.metrics import record_workflow_execution
from app.utils.response import AppError

if TYPE_CHECKING:
    from app.service.workflow_service import WorkflowService

log = get_logger("workflow_runner")


async def run_execution(
    svc: "WorkflowService",
    execution_id: str,
    definition: dict,
    inputs: dict,
    chat_id: str,
    user_id: str,
    resume_from: Optional[str] = None,
    context_mod: Optional[dict] = None,
    workflow_id: str = "",
    exec_stack: Optional[list] = None,
    tenant_id: str = "default",
) -> None:
    """异步推进工作流执行

    Sprint 8.1: 从 WorkflowService._run_execution 迁移为模块级函数。
    """
    final_status = "failed"
    outputs: dict = {}
    try:
        wf_def = WorkflowDefinition(definition)
        context = {
            "inputs": inputs,
            "variables": dict(inputs),
            "outputs": {},
            "__adj": wf_def.adj,
            "__current_workflow_id": workflow_id,
            "__exec_stack": exec_stack if exec_stack is not None else [],
            "__tenant_id": tenant_id,  # Sprint 13: 节点级租户传播（skill_call/agent_call/sub_workflow）
        }
        if context_mod:
            context["variables"].update(context_mod)

        current = resume_from or wf_def.start_node
        while current:
            await update_execution(execution_id, current_node=current, status="running")
            node = wf_def.nodes[current]
            output, next_ids = await execute_node(node, context, chat_id, user_id)

            if output.get("paused"):
                await update_execution(execution_id, status="paused")
                log.info("Workflow {} paused at {} for HITL", execution_id, current)
                final_status = "paused"
                return

            if output.get("finished"):
                outputs = context.get("outputs", {})
                await update_execution(execution_id, status="completed", outputs=outputs)
                log.info("Workflow {} completed", execution_id)
                final_status = "completed"
                return

            # 推进到下一节点
            if not next_ids:
                outputs = context.get("outputs", {})
                await update_execution(execution_id, status="completed", outputs=outputs)
                final_status = "completed"
                return
            current = next_ids[0]  # 简化：取第一个分支

    except AppError as e:
        log.exception("Workflow {} failed: {}", execution_id, e.message)
        await update_execution(
            execution_id, status="failed",
            error={"code": e.code, "message": e.message},
        )
    except Exception as e:
        log.exception("Workflow {} crashed: {}", execution_id, e)
        await update_execution(
            execution_id, status="failed", error={"message": str(e)},
        )
    finally:
        # 审计工作流执行结果 + 指标采集（非阻塞，paused 不审计终态）
        if final_status in ("completed", "failed"):
            record_workflow_execution(final_status)
            from app.service.audit_service import audit_service
            from app.utils.context import get_request_id
            try:
                await audit_service.log_action(
                    user_id=user_id, action="workflow.execute",
                    workflow_id=execution_id, input_data=inputs,
                    output_data=outputs, status=final_status,
                    request_id=get_request_id(), tenant_id=tenant_id,
                )
            except Exception as audit_e:
                log.warning("audit workflow {} failed: {}", execution_id, audit_e)

        # Sprint 6.3: IM 双向回调 — IM 触发的工作流完成后，结果回传原会话
        if chat_id and final_status in ("completed", "failed"):
            try:
                from app.service.im_push_service import im_push_service
                await im_push_service.push_workflow_result(
                    workflow_id, chat_id, inputs, outputs, final_status,
                )
            except Exception as cb_e:
                log.warning("IM callback for workflow {} failed: {}", execution_id, cb_e)


async def update_execution(
    execution_id: str,
    status: Optional[str] = None,
    current_node: Optional[str] = None,
    outputs: Optional[dict] = None,
    error: Optional[dict] = None,
) -> None:
    """更新执行实例状态

    Sprint 8.1: 从 WorkflowService._update_execution 迁移为模块级函数。
    """
    update_data: dict = {}
    if status:
        update_data["status"] = status
        if status in ("completed", "failed", "cancelled"):
            update_data["finished_at"] = datetime.now()
    if current_node is not None:
        update_data["current_node"] = current_node
    if outputs is not None:
        update_data["outputs"] = outputs
    if error is not None:
        update_data["error"] = error

    from sqlalchemy import update as sa_update
    async with get_db_session() as session:
        await session.execute(
            sa_update(WorkflowExecutionORM)
            .where(WorkflowExecutionORM.id == execution_id)
            .values(**update_data)
        )