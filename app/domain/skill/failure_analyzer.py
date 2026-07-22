"""SkillFailureAnalyzer - 失败优先学习引擎

核心思想：失败经验比成功经验更有价值。分析失败任务 → 提炼"避坑Skill"。

流程：
  1. 加载失败任务的轨迹（消息 + 步骤）
  2. LLM 分析失败根因 + 避坑建议
  3. 若失败可复用 → 生成 SkillDraftORM（origin=failure_analyzer）
  4. 用户审查后可转为正式 Skill

触发时机：
  - Agent 任务 FAILED 时自动触发（agent_service hook）
  - Skill 执行失败累计达阈值时触发（optimizer 调用）

架构：Domain 层，延迟 import Infra/Service 避免循环依赖。
"""
import json

from sqlalchemy import select

from app.infra.db.models_agent import AgentTaskORM, AgentTaskStepORM
from app.infra.db.models_user_skill import SkillDraftORM
from app.infra.db.session import get_db_session
from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger

log = get_logger("skill_failure_analyzer")

ANALYSIS_PROMPT = """你是企业自动化平台的失败分析专家。给定一个失败的 Agent 任务轨迹，分析失败根因并判断是否值得沉淀为"避坑Skill"。

输出 JSON：
{
  "failure_root_cause": "失败根因（1句话）",
  "failure_category": "tool_error|param_error|logic_error|external_error|permission_error",
  "avoidable": true/false,
  "avoidance_advice": "如何避免此类失败（1-2句）",
  "worth_sediment": true/false,
  "skill_draft": {
    "name": "避坑-Skill名（≤20字）",
    "description": "这个避坑Skill防止什么问题",
    "tags": ["避坑", "失败教训"],
    "confidence": 0.0-1.0,
    "reasoning": "为什么值得/不值得沉淀"
  }
}

规则：
1. worth_sediment=false 当失败是偶发的（网络抖动/限流），不值得沉淀
2. worth_sediment=true 当失败是结构性的（参数模式错误/工具选错/逻辑遗漏）
3. confidence < 0.5 表示不确定，仍可生成草稿供人工判断
4. skill_draft.name 用"避坑-"前缀突出风险类型"""


async def analyze_failure(task_id: str) -> dict:
    """分析失败任务，生成避坑Skill草稿

    Returns: {analyzed, worth_sediment, draft_id?, root_cause, category}
    """
    async with get_db_session() as session:
        task = await session.get(AgentTaskORM, task_id)
        if task is None:
            return {"analyzed": False, "reason": "task_not_found"}
        # 只分析失败任务
        if task.status != "failed":
            return {"analyzed": False, "reason": "task_not_failed"}

        stmt = select(AgentTaskStepORM).where(
            AgentTaskStepORM.task_id == task_id
        ).order_by(AgentTaskStepORM.step_index)
        steps = (await session.execute(stmt)).scalars().all()

    trace = _build_failure_trace(task, steps)
    result = await llm_json_call(ANALYSIS_PROMPT, trace, temperature=0.2, max_tokens=600)

    worth = result.get("worth_sediment", False)
    root_cause = result.get("failure_root_cause", "unknown")
    category = result.get("failure_category", "unknown")

    log.info(
        "Failure analyzed: task={} worth={} category={} cause={}",
        task_id, worth, category, root_cause[:80],
    )

    if not worth:
        return {
            "analyzed": True, "worth_sediment": False,
            "root_cause": root_cause, "category": category,
        }

    # 生成避坑Skill草稿
    draft_data = result.get("skill_draft", {})
    draft_id = await _create_avoidance_draft(
        task_id=task_id,
        draft_data=draft_data,
        root_cause=root_cause,
        advice=result.get("avoidance_advice", ""),
        tenant_id=task.tenant_id,
    )

    return {
        "analyzed": True, "worth_sediment": True,
        "draft_id": draft_id, "root_cause": root_cause,
        "category": category, "advice": result.get("avoidance_advice", ""),
    }


async def _create_avoidance_draft(
    task_id: str, draft_data: dict, root_cause: str, advice: str,
    tenant_id: str = "default",
) -> str:
    """持久化避坑Skill草稿到 SkillDraftORM（tenant_id 落来源任务租户）"""
    name = draft_data.get("name", f"避坑-{task_id[:8]}")
    # 避免重名：追加 task_id 前缀
    if not name.startswith("避坑-"):
        name = "避坑-" + name

    reasoning = draft_data.get("reasoning", "")
    full_reasoning = f"根因: {root_cause}\n建议: {advice}\n{reasoning}"

    async with get_db_session() as session:
        draft = SkillDraftORM(
            name=name,
            description=draft_data.get("description", "失败避坑Skill"),
            input_schema={"type": "object", "properties": {}},
            source_type="workflow",
            source_ref="",  # 待用户关联 workflow
            tags=draft_data.get("tags", ["避坑", "失败教训"]),
            confidence=float(draft_data.get("confidence", 0.5)),
            reasoning=full_reasoning,
            origin="failure_analyzer",
            task_id=task_id,
            status="pending",
            tenant_id=tenant_id,
        )
        session.add(draft)
        await session.flush()
        log.info("Avoidance draft created: {} (task={})", draft.id, task_id)
        return draft.id


def _build_failure_trace(task: AgentTaskORM, steps: list) -> str:
    """构造失败轨迹摘要（突出失败点）"""
    lines = [
        f"用户消息：{task.original_message or '(空)'}",
        "任务状态：FAILED",
        f"错误信息：{(task.error or {}).get('message', 'unknown') if task.error else 'unknown'}",
        "",
        "执行轨迹：",
    ]
    for i, s in enumerate(steps):
        status_mark = "❌" if s.status == "failed" else "✓" if s.status == "completed" else "○"
        args_str = json.dumps(s.tool_input, ensure_ascii=False)[:150] if s.tool_input else "{}"
        lines.append(f"{i+1}. {status_mark} 工具：{s.tool_name or '(无)'} 状态：{s.status}")
        lines.append(f"   入参：{args_str}")
        if s.status == "failed" and s.error:
            lines.append(f"   错误：{str(s.error)[:200]}")
        elif s.tool_output:
            out_str = json.dumps(s.tool_output, ensure_ascii=False)[:100]
            lines.append(f"   出参：{out_str}")
    return "\n".join(lines)
