"""SkillRecorder - 从 Agent 任务轨迹录制可复用 Workflow + Skill

将 AgentTaskStepORM 的工具调用序列转化为 workflow definition：
  start → skill_call_1 → skill_call_2 → ... → end

每个 skill_call 节点的 config 含 skill_id + args 模板。
LLM 辅助增强（渐进增强，LLM 不可用时降级为简单复制）：
  - 智能命名：根据任务消息生成有意义的 workflow name/description
  - 变量提取：识别哪些 args 应参数化为 ${var}（如查询词、日期）vs 固定值（如 page_size）

录制产物是 WorkflowORM（definition 持久化），Skill 通过 source_type=workflow 引用。

架构说明：
  本模块位于 Domain 层，通过函数内延迟 import app.service.* 调用 Service 层
  （避免循环依赖，同 node_executors.py 模式）。
"""
import json
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
    LLM 可用时辅助变量提取 + 智能命名（降级为简单复制）。

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
    step_records = []  # 保存有效步骤信息供 LLM 增强
    for i, step in enumerate(steps):
        skill_id = await skill_service.find_skill_id_by_name(step.tool_name)
        if not skill_id:
            log.warning("skip step {}: skill {} not found", i, step.tool_name)
            continue
        node_id = f"call_{i}"
        args_template = _args_to_template(step.tool_input)
        nodes.append({
            "id": node_id, "type": "skill_call",
            "config": {"skill_id": skill_id, "args": args_template},
        })
        edges.append({"source": prev_id, "target": node_id})
        prev_id = node_id
        valid_steps += 1
        step_records.append({
            "node_id": node_id, "tool_name": step.tool_name,
            "args": args_template,
        })
    if valid_steps == 0:
        raise AppError(ErrorCode.WORKFLOW_INVALID, "任务步骤无可复用的 skill 调用", 400)
    nodes.append({"id": "end", "type": "end", "config": {}})
    edges.append({"source": prev_id, "target": "end"})

    definition = {"nodes": nodes, "edges": edges, "variables": []}

    # LLM 辅助增强：智能命名 + 变量提取（降级为简单模式）
    enhanced = await _enhance_recording_with_llm(task, step_records, definition)
    wf_name = enhanced["name"]
    wf_desc = enhanced["description"]
    definition = enhanced["definition"]

    async with get_db_session() as session:
        wf = WorkflowORM(
            name=wf_name, description=wf_desc,
            definition=definition, enabled=True, created_by=user_id or None,
        )
        session.add(wf)
        await session.flush()
        log.info("Workflow recorded from task {}: {} ({} steps)", task_id, wf.id, valid_steps)
        return {
            "workflow_id": wf.id, "name": wf_name,
            "step_count": valid_steps, "definition": definition,
        }


# ============ LLM 辅助增强（渐进增强，降级安全） ============

_ENHANCE_PROMPT = """你是 Workflow 录制增强助手。给定 Agent 任务的执行轨迹，生成更友好的 workflow 元数据 + 变量提取。

任务：
1. 生成简洁有意义的 workflow 名称（中文，≤20字，反映核心动作）
2. 生成用途说明（中文，1句话）
3. 识别哪些工具参数应参数化为变量（${var_name}），规则：
   - 查询词、搜索关键词、日期、用户输入 → 变量（如 ${query}, ${date}）
   - 固定配置（如 page_size=10, format=json）→ 保持原值

输出 JSON（仅 JSON）：
{
  "name": "workflow 名称",
  "description": "用途说明",
  "variables": [
    {"node_id": "call_0", "arg_key": "query", "var_name": "search_keyword"}
  ]
}"""


async def _enhance_recording_with_llm(
    task: AgentTaskORM, step_records: list[dict], definition: dict,
) -> dict:
    """LLM 辅助增强录制：智能命名 + 变量提取

    LLM 不可用时降级为简单命名（截断 original_message）。
    """
    # 默认降级值
    fallback_name = f"录制-{(task.original_message or 'task')[:20]}-{task.id[:8]}"
    fallback = {
        "name": fallback_name,
        "description": f"从任务 {task.id} 录制",
        "variables": [],
    }

    try:
        from app.utils.llm_structured import llm_json_call
        trace = _build_trace_for_enhance(task, step_records)
        parsed = await llm_json_call(
            _ENHANCE_PROMPT, trace,
            temperature=0.3, max_tokens=400, fallback=fallback,
        )
    except Exception as e:
        log.warning("LLM enhance recording failed, fallback to simple: {}", e)
        return {"name": fallback_name, "description": fallback["description"], "definition": definition}

    name = (parsed.get("name") or fallback_name)[:50]
    desc = (parsed.get("description") or fallback["description"])[:200]
    variables = parsed.get("variables") or []
    if not isinstance(variables, list):
        variables = []

    # 应用变量提取到 definition
    enhanced_def = _apply_variables(definition, variables)
    log.info("Recording enhanced: name={} vars={}", name, len(variables))
    return {"name": name, "description": desc, "definition": enhanced_def}


def _build_trace_for_enhance(task: AgentTaskORM, step_records: list[dict]) -> str:
    """构造 LLM 增强用的轨迹摘要"""
    lines = [f"用户消息：{task.original_message or '(空)'}", "", "工具调用序列："]
    for s in step_records:
        args_str = json.dumps(s["args"], ensure_ascii=False, default=str)[:200]
        lines.append(f"- {s['node_id']}: {s['tool_name']} args={args_str}")
    return "\n".join(lines)


def _apply_variables(definition: dict, variables: list[dict]) -> dict:
    """将变量建议应用到 workflow definition

    将指定 node 的 arg 值替换为 ${var_name}，并在 definition.variables 注册变量。
    """
    if not variables:
        return definition
    var_defs = []
    for v in variables:
        node_id = v.get("node_id")
        arg_key = v.get("arg_key")
        var_name = v.get("var_name")
        if not node_id or not arg_key or not var_name:
            continue
        # 找到对应节点，替换 arg 值
        for node in definition.get("nodes", []):
            if node.get("id") == node_id:
                args = node.get("config", {}).get("args", {})
                if arg_key in args:
                    original_value = args[arg_key]  # 先保存原值，再替换为占位符
                    args[arg_key] = f"${{{var_name}}}"
                    var_defs.append({
                        "name": var_name,
                        "type": "string",
                        "default": str(original_value)[:100],
                    })
                break
    if var_defs:
        definition["variables"] = var_defs
    return definition


# ============ 基础录制函数 ============

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

    LLM 增强后会进一步将部分值替换为 ${var} 变量引用。
    """
    if not tool_input:
        return {}
    return dict(tool_input)
