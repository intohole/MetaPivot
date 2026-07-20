"""WorkflowService - 工作流 CRUD 与执行编排

职责：
1. 工作流定义 CRUD（持久化）
2. 触发执行（创建 WorkflowExecution + 异步推进 DAG）
3. 查询执行状态
4. HITL 暂停/恢复

Sprint 8.1: DAG 推进逻辑已抽离到 workflow_runner.py（保持本文件 ≤ 300 行）。
"""
import asyncio
from typing import Optional

from sqlalchemy import func, select

from app.domain.workflow.engine import WorkflowDefinition
from app.infra.db.models_core import WorkflowExecutionORM, WorkflowORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("workflow_service")


class WorkflowService:
    """工作流服务单例"""

    # 运行中执行任务引用（避免被GC）
    _running_tasks: dict[str, asyncio.Task] = {}

    # ============ CRUD ============

    async def create_workflow(self, data: dict, created_by: str = "") -> dict:
        WorkflowDefinition(data["definition"])
        from app.domain.workflow.trigger_spec import parse_trigger
        trigger_spec = parse_trigger(data.get("trigger"))
        trigger_dict = trigger_spec.to_dict()

        async with get_db_session() as session:
            wf = WorkflowORM(
                name=data["name"], description=data.get("description", ""),
                definition=data["definition"], trigger=trigger_dict,
                enabled=data.get("enabled", True), created_by=created_by or None,
            )
            session.add(wf)
            await session.flush()
            # Phase 2: webhook 类型自动创建关联 WebhookORM，回填 token
            if trigger_spec.type == "webhook":
                try:
                    from app.service.webhook_service import webhook_service
                    hook = await webhook_service.create_webhook(
                        name=f"workflow:{wf.name}", target_type="workflow",
                        target_id=wf.id, created_by=created_by,
                    )
                    trigger_dict["webhook_token"] = hook["token"]
                    wf.trigger = trigger_dict
                    await session.flush()
                except Exception as e:
                    log.warning("auto-create webhook for workflow {} failed: {}", wf.id, e)
            log.info("Workflow created: {} ({})", wf.name, wf.id)
            return {
                "id": wf.id, "name": wf.name, "status": "created",
                "trigger": trigger_dict,
                "created_at": wf.created_at.isoformat() if wf.created_at else None,
            }

    async def list_workflows(
        self,
        page: int = 1,
        page_size: int = 20,
        enabled: Optional[bool] = None,
        keyword: str = "",
    ) -> tuple[list[WorkflowORM], int]:
        async with get_db_session() as session:
            stmt = select(WorkflowORM)
            if enabled is not None:
                stmt = stmt.where(WorkflowORM.enabled == enabled)
            if keyword:
                stmt = stmt.where(WorkflowORM.name.ilike(f"%{keyword}%"))
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(WorkflowORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            items = (await session.execute(stmt)).scalars().all()
            return items, total

    async def get_workflow(self, workflow_id: str) -> WorkflowORM:
        async with get_db_session() as session:
            wf = await session.get(WorkflowORM, workflow_id)
            if wf is None:
                raise AppError(ErrorCode.WORKFLOW_NOT_FOUND, status_code=404)
            return wf

    async def update_workflow(self, workflow_id: str, update_data: dict) -> dict:
        async with get_db_session() as session:
            wf = await session.get(WorkflowORM, workflow_id)
            if wf is None:
                raise AppError(ErrorCode.WORKFLOW_NOT_FOUND, status_code=404)
            if "definition" in update_data:
                WorkflowDefinition(update_data["definition"])
                wf.version += 1
            # Phase 2: trigger 变更时重新校验
            if "trigger" in update_data:
                from app.domain.workflow.trigger_spec import parse_trigger
                update_data["trigger"] = parse_trigger(update_data["trigger"]).to_dict()
            for k, v in update_data.items():
                if hasattr(wf, k) and v is not None:
                    setattr(wf, k, v)
            await session.flush()
            # refresh 以获取 onupdate=func.now() 生成的 updated_at（避免 async lazy load 报错）
            await session.refresh(wf)
            return {"id": wf.id, "updated_at": wf.updated_at.isoformat() if wf.updated_at else None}

    async def delete_workflow(self, workflow_id: str) -> dict:
        async with get_db_session() as session:
            wf = await session.get(WorkflowORM, workflow_id)
            if wf is None:
                raise AppError(ErrorCode.WORKFLOW_NOT_FOUND, status_code=404)
            await session.delete(wf)
            return {"id": workflow_id, "deleted": True}

    # ============ 执行 ============

    async def execute_workflow(
        self,
        workflow_id: str,
        inputs: dict,
        chat_id: str = "",
        user_id: str = "",
        exec_stack: Optional[list] = None,
    ) -> dict:
        """触发工作流执行（异步推进 DAG）。exec_stack 用于 Phase 2 循环检测。"""
        wf = await self.get_workflow(workflow_id)
        if not wf.enabled:
            raise AppError(ErrorCode.WORKFLOW_INVALID, "工作流已禁用", 400)

        async with get_db_session() as session:
            execution = WorkflowExecutionORM(
                workflow_id=workflow_id, status="running", inputs=inputs,
                triggered_by=user_id or None, trigger_channel="api",
                chat_id=chat_id or None, current_node="",
            )
            session.add(execution)
            await session.flush()
            execution_id = execution.id

        # Sprint 8.1: 委托到 workflow_runner.run_execution（保持本文件 ≤ 300 行）
        from app.service.workflow_runner import run_execution
        import asyncio
        bg = asyncio.create_task(run_execution(
            self, execution_id, wf.definition, inputs, chat_id, user_id,
            workflow_id=workflow_id, exec_stack=exec_stack,
        ))
        self._running_tasks[execution_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(execution_id, None))
        return {"execution_id": execution_id, "status": "pending"}

    async def get_execution(self, execution_id: str) -> dict:
        async with get_db_session() as session:
            ex = await session.get(WorkflowExecutionORM, execution_id)
            if ex is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行实例不存在", 404)
            return {
                "execution_id": ex.id, "workflow_id": ex.workflow_id,
                "status": ex.status, "current_node": ex.current_node,
                "inputs": ex.inputs, "outputs": ex.outputs,
                "started_at": ex.started_at.isoformat() if ex.started_at else None,
                "finished_at": ex.finished_at.isoformat() if ex.finished_at else None,
                "error": ex.error,
            }

    async def resume_execution(self, execution_id: str, decision: str, modifications: dict) -> dict:
        """HITL 恢复执行"""
        async with get_db_session() as session:
            ex = await session.get(WorkflowExecutionORM, execution_id)
            if ex is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行实例不存在", 404)
            if ex.status != "paused":
                raise AppError(ErrorCode.WORKFLOW_INVALID, "执行未暂停", 400)

        # Sprint 8.1: 委托到 workflow_runner.update_execution
        from app.service.workflow_runner import run_execution, update_execution
        if decision == "reject":
            await update_execution(execution_id, status="cancelled", error={"reason": "user_rejected"})
            return {"execution_id": execution_id, "status": "cancelled"}

        # 恢复：重新加载工作流定义并从当前节点继续
        wf = await self.get_workflow(ex.workflow_id)
        import asyncio
        bg = asyncio.create_task(run_execution(
            self, execution_id, wf.definition, ex.inputs, ex.chat_id or "", ex.triggered_by or "",
            resume_from=ex.current_node, context_mod=modifications,
            workflow_id=ex.workflow_id,
        ))
        self._running_tasks[execution_id] = bg
        bg.add_done_callback(lambda t: self._running_tasks.pop(execution_id, None))
        return {"execution_id": execution_id, "status": "running"}


workflow_service = WorkflowService()