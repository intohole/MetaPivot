"""SkillRecorder - 从 Agent 任务轨迹录制可复用 Workflow + Skill

将 AgentTaskStepORM 的工具调用序列转化为 workflow definition：
  start → skill_call_1 → skill_call_2 → ... → end

每个 skill_call 节点的 config 含 skill_id + args 模板（保留原始入参作为默认值）。
录制产物是 WorkflowORM（definition 持久化），Skill 通过 source_type=workflow 引用。

架构说明：
  本模块位于 Domain 层，通过函数内延迟 import app.service.* 调用 Service 层
  （避免循环依赖，同 node_executors.py 模式）。
"""
from typing import Optional

from sqlalchemy import select

from app.infra.db.models_core import AgentTaskORM, AgentTaskStepORM, WorkflowORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("skill_recorder")


async def record_task_to_workflow(task_id: str, user_id: str = "") -> dict:
    """从 Agent 任务录制 workflow definition

    读取 AgentTaskStepORM 的 completed 工具调用序列，
    生成 start → skill_call_* → end 的 workflow definition 并持久化。

    Returns: {workflow_id, name, step_count, definition}
    """
    from app.service.skill_service import skill_service  # 延迟 import 避免循环

    async with get_db_session() as session:
        task = await session.get(AgentTaskORM, task_id)
        if task is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在", 404)
        stmt = select(AgentTaskStepORM).where(
            AgentTaskStepORM.task_id == task_id,
            AgentTaskStepORM.tool_name.isnot(None),
            AgentTaskStepORM.status == "completed",
        ).order_by(AgentTaskStepORM.step_index)
        steps = (await session.execute(stmt)).scalars().all()

    if not steps:
        raise AppError(ErrorCode.WORKFLOW_INVALID, "任务无有效工具调用步骤", 400)

    # 构造 workflow definition: start → skill_call_* → end
    nodes: list[dict] = [{"id": "start", "type": "start", "config": {}}]
    edges: list[dict] = []
    prev_id = "start"
    valid_steps = 0
    for i, step in enumerate(steps):
        skill_id = await skill_service.find_skill_id_by_name(step.tool_name)
        if not skill_id:
            log.warning("skip step {}: skill {} not found", i, step.tool_name)
            continue
        node_id = f"call_{i}"
        nodes.append({
            "id": node_id, "type": "skill_call",
            "config": {"skill_id": skill_id, "args": _args_to_template(step.tool_input)},
        })
        edges.append({"source": prev_id, "target": node_id})
        prev_id = node_id
        valid_steps += 1
    if valid_steps == 0:
        raise AppError(ErrorCode.WORKFLOW_INVALID, "任务步骤无可复用的 skill 调用", 400)
    nodes.append({"id": "end", "type": "end", "config": {}})
    edges.append({"source": prev_id, "target": "end"})

    definition = {"nodes": nodes, "edges": edges, "variables": []}
    wf_name = f"录制-{(task.original_message or 'task')[:20]}-{task_id[:8]}"

    async with get_db_session() as session:
        wf = WorkflowORM(
            name=wf_name, description=f"从任务 {task_id} 录制",
            definition=definition, enabled=True, created_by=user_id or None,
        )
        session.add(wf)
        await session.flush()
        log.info("Workflow recorded from task {}: {} ({} steps)", task_id, wf.id, valid_steps)
        return {
            "workflow_id": wf.id, "name": wf_name,
            "step_count": valid_steps, "definition": definition,
        }


async def create_skill_from_workflow(
    workflow_id: str, name: str, description: str,
    owner_id: str = "", tags: Optional[list] = None,
) -> dict:
    """从现有 workflow 快捷创建 source_type=workflow 的 skill"""
    from app.service.skill_service import skill_service
    from app.service.workflow_service import workflow_service
    await workflow_service.get_workflow(workflow_id)  # 校验 workflow 存在
    data = {
        "name": name, "description": description,
        "input_schema": {"type": "object", "properties": {}},
        "source_type": "workflow", "source_ref": workflow_id,
        "permission": "user", "require_confirm": False,
        "tags": tags or [],
    }
    return await skill_service.create_skill(data, owner_id=owner_id)


async def record_task_to_skill(
    task_id: str, name: str, description: str,
    owner_id: str = "", tags: Optional[list] = None,
) -> dict:
    """从 agent 任务录制 workflow + 创建 skill（一键沉淀）

    组合 record_task_to_workflow + create_skill_from_workflow。
    """
    rec = await record_task_to_workflow(task_id, user_id=owner_id)
    return await create_skill_from_workflow(
        rec["workflow_id"], name, description, owner_id=owner_id, tags=tags,
    )


def _args_to_template(tool_input: Optional[dict]) -> dict:
    """将工具入参转为 workflow 模板（保留原始值作为默认参数）

    用户可在 workflow 编辑器中将固定值改为 ${var} 变量引用。
    """
    if not tool_input:
        return {}
    return dict(tool_input)
