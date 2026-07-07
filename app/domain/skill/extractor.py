"""SkillExtractor - LLM 从 Agent 任务轨迹抽取 skill manifest 草稿

输入: AgentTaskORM.original_message + AgentTaskStepORM 列表(tool_name/tool_input/tool_output)
输出: {name, description, input_schema, suggested_tags, confidence, reasoning, recommended_source, task_id, step_count}

LLM 调用复用 LLMProvider.chat_completion(response_format={type: json_object})。
不持久化草稿，前端 review 后调 POST /skills/from-task 持久化（录制为 workflow + skill）。

架构说明：
  本模块位于 Domain 层，通过延迟 import 调用 Infra 层（LLM Provider）+ Data 层（ORM）。
"""
import json
from typing import Optional

from sqlalchemy import select

from app.infra.db.models_core import AgentTaskORM, AgentTaskStepORM
from app.infra.db.session import get_db_session
from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("skill_extractor")

SYSTEM_PROMPT = """你是企业办公自动化平台的 Skill 提取助手。给定一个 Agent 任务的执行轨迹（用户消息 + 工具调用序列），你的任务是抽取可复用的 Skill manifest。

输出 JSON 格式：
{
  "name": "简洁的 skill 名称（中文，≤20字，唯一）",
  "description": "skill 用途说明（中文，1-2句）",
  "input_schema": {"type": "object", "properties": {...}, "required": [...]},
  "suggested_tags": ["标签1", "标签2"],
  "confidence": 0.0-1.0,
  "reasoning": "为什么这样抽取（1句）",
  "recommended_source": "recorded | workflow | function"
}

规则：
1. name 应反映核心动作，如"查询订单状态"、"发送日报"
2. input_schema 的 properties 从工具入参中提取可参数化字段（如 order_id, date）
3. confidence < 0.6 表示轨迹不适合沉淀（步骤过于发散）
4. recommended_source: 步骤线性可复用→recorded；已有 workflow→workflow；单一函数→function"""


async def extract_skill_from_task(task_id: str) -> dict:
    """LLM 从 agent 任务轨迹抽取 skill 草稿

    不持久化，返回草稿供前端 review。
    """
    async with get_db_session() as session:
        task = await session.get(AgentTaskORM, task_id)
        if task is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在", 404)
        stmt = select(AgentTaskStepORM).where(
            AgentTaskStepORM.task_id == task_id,
            AgentTaskStepORM.tool_name.isnot(None),
        ).order_by(AgentTaskStepORM.step_index)
        steps = (await session.execute(stmt)).scalars().all()

    if not steps:
        raise AppError(ErrorCode.WORKFLOW_INVALID, "任务无工具调用步骤", 400)

    trace_summary = _build_trace_summary(task, steps)
    draft = await llm_json_call(SYSTEM_PROMPT, trace_summary, temperature=0.3, max_tokens=800)
    draft["task_id"] = task_id
    draft["step_count"] = len(steps)
    # LLM 偶发漏字段时降级为 0.0，避免 log.info 格式化 None 报错
    confidence = draft.get("confidence") or 0.0
    log.info("Skill extracted from task {}: confidence={}", task_id, confidence)
    return draft


def _build_trace_summary(task: AgentTaskORM, steps: list) -> str:
    """构造轨迹摘要（控制 token，避免超长）"""
    lines = [
        f"用户消息：{task.original_message or '(空)'}",
        f"意图：{task.intent or 'unknown'}",
        "",
        "工具调用序列：",
    ]
    for i, s in enumerate(steps):
        args_str = json.dumps(s.tool_input, ensure_ascii=False)[:200] if s.tool_input else "{}"
        out_str = json.dumps(s.tool_output, ensure_ascii=False)[:150] if s.tool_output else "{}"
        lines.append(f"{i+1}. 工具：{s.tool_name}")
        lines.append(f"   入参：{args_str}")
        lines.append(f"   出参：{out_str}")
    return "\n".join(lines)
