"""SkillEvolution - 经验固化器 + Review 系统

职责：
  1. 经验固化：任务成功完成后，判断是否值得沉淀为 Skill 草稿
  2. Review 系统：管理草稿审批 + 修订审批（PR-like workflow）
  3. 协调 reflector/failure_analyzer/optimizer 三个引擎

架构：Domain 层，编排其他 skill 子模块，提供 Service 层调用的统一入口。
"""
import json
from datetime import datetime

from sqlalchemy import select

from app.infra.db.models_agent import AgentTaskORM, AgentTaskStepORM
from app.infra.db.models_user_skill import (
    SkillDraftORM, SkillORM, SkillRevisionORM,
)
from app.infra.db.session import get_db_session
from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger

log = get_logger("skill_evolution")

# 经验固化：判断成功任务是否值得沉淀
SOLIDIFY_PROMPT = """你是企业自动化的经验固化助手。给定一个成功的 Agent 任务轨迹，判断是否值得沉淀为可复用 Skill。

任务轨迹：
{trace}

输出 JSON：
{{
  "worth_solidify": true/false,
  "reusability": "high|medium|low",
  "skill_draft": {{
    "name": "Skill 名称（≤20字，反映核心动作）",
    "description": "Skill 用途（1-2句）",
    "tags": ["标签1"],
    "confidence": 0.0-1.0,
    "reasoning": "为什么值得沉淀"
  }}
}}

规则：
1. worth_solidify=false 当任务是简单问答（无需工具）或一次性任务
2. worth_solidify=true 当任务有 ≥2 个工具调用且模式可复用
3. reusability=high 当模式清晰且常见（如查询+通知、检索+总结）
4. confidence < 0.5 表示模式不够清晰，仍可生成草稿供人工判断"""


async def try_solidify_experience(task_id: str) -> dict:
    """经验固化：成功任务完成后尝试沉淀为 Skill 草稿

    Returns: {solidified, draft_id?, reason}
    """
    async with get_db_session() as session:
        task = await session.get(AgentTaskORM, task_id)
        if task is None:
            return {"solidified": False, "reason": "task_not_found"}
        if task.status != "completed":
            return {"solidified": False, "reason": "task_not_completed"}

        stmt = select(AgentTaskStepORM).where(
            AgentTaskStepORM.task_id == task_id,
            AgentTaskStepORM.tool_name.isnot(None),
            AgentTaskStepORM.status == "completed",
        ).order_by(AgentTaskStepORM.step_index)
        steps = (await session.execute(stmt)).scalars().all()

    if len(steps) < 2:
        return {"solidified": False, "reason": "insufficient_steps"}

    trace = _build_success_trace(task, steps)
    result = await llm_json_call(SOLIDIFY_PROMPT, trace, temperature=0.3, max_tokens=500)

    if not result.get("worth_solidify", False):
        return {"solidified": False, "reason": "not_reusable"}

    draft_data = result.get("skill_draft", {})
    reusability = result.get("reusability", "low")

    # 先录制 workflow（复用 recorder），再创建草稿引用
    from app.domain.skill.recorder import record_task_to_workflow
    try:
        wf_rec = await record_task_to_workflow(task_id, user_id="")
        workflow_id = wf_rec["workflow_id"]
    except Exception as e:
        log.warning("Solidify: record workflow failed for task {}: {}", task_id, e)
        return {"solidified": False, "reason": "record_failed"}

    draft_id = await _create_draft(
        task_id=task_id, workflow_id=workflow_id,
        draft_data=draft_data, reusability=reusability,
    )
    return {"solidified": True, "draft_id": draft_id, "reusability": reusability}


# ============ Review 系统 ============

async def list_drafts(
    status: str = "pending", owner_id: str = "", page: int = 1, page_size: int = 20,
) -> tuple[list, int]:
    """列出 Skill 草稿（待审核/已批准/已拒绝）"""
    from sqlalchemy import func
    async with get_db_session() as session:
        stmt = select(SkillDraftORM).where(SkillDraftORM.status == status)
        if owner_id:
            stmt = stmt.where(SkillDraftORM.owner_id == owner_id)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(SkillDraftORM.created_at.desc()).offset((page-1)*page_size).limit(page_size)
        items = (await session.execute(stmt)).scalars().all()
    return [_draft_to_dict(d) for d in items], total


async def approve_draft(draft_id: str, user_id: str = "") -> dict:
    """批准草稿 → 转为正式 Skill"""
    async with get_db_session() as session:
        draft = await session.get(SkillDraftORM, draft_id)
        if draft is None:
            return {"approved": False, "reason": "draft_not_found"}
        if draft.status != "pending":
            return {"approved": False, "reason": "already_processed"}

        # 名称冲突检查：避免 IntegrityError（skills.name 有 unique 约束）
        existing = await session.execute(
            select(SkillORM.id).where(SkillORM.name == draft.name)
        )
        if existing.scalar_one_or_none():
            return {"approved": False, "reason": "name_conflict",
                    "message": f"Skill 名称 '{draft.name}' 已存在，请先重命名草稿"}

        # source_ref 空值处理：避坑类草稿可能无 source_ref（待用户关联 workflow），
        # 允许审批但创建为 disabled 状态，用户配置 source_ref 后手动启用
        needs_source_ref = not draft.source_ref

        # 创建正式 Skill
        skill = SkillORM(
            name=draft.name, description=draft.description,
            input_schema=draft.input_schema, source_type=draft.source_type,
            source_ref=draft.source_ref or "manual:configure-required",
            tags=draft.tags,
            owner_id=draft.owner_id or user_id or None,
            visibility="private", version=1, enabled=not needs_source_ref,
            changelog=[{"version": 1, "change": f"from draft({draft.origin})" + (" (disabled: source_ref未配置)" if needs_source_ref else ""), "at": datetime.now().isoformat()}],
        )
        session.add(skill)
        draft.status = "approved"
        draft.owner_id = user_id or draft.owner_id
        await session.flush()
        log.info("Draft approved: {} → skill {}", draft_id, skill.id)
        return {"approved": True, "skill_id": skill.id}


async def reject_draft(draft_id: str, user_id: str = "") -> dict:
    """拒绝草稿"""
    async with get_db_session() as session:
        draft = await session.get(SkillDraftORM, draft_id)
        if draft is None:
            return {"rejected": False, "reason": "draft_not_found"}
        draft.status = "rejected"
        await session.flush()
        log.info("Draft rejected: {} by {}", draft_id, user_id or "system")
        return {"rejected": True, "draft_id": draft_id}


async def list_revisions(
    skill_id: str = "", status: str = "", page: int = 1, page_size: int = 20,
) -> tuple[list, int]:
    """列出 Skill 修订记录（PR-like Review）"""
    from sqlalchemy import func
    async with get_db_session() as session:
        stmt = select(SkillRevisionORM)
        if skill_id:
            stmt = stmt.where(SkillRevisionORM.skill_id == skill_id)
        if status:
            stmt = stmt.where(SkillRevisionORM.status == status)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(SkillRevisionORM.created_at.desc()).offset((page-1)*page_size).limit(page_size)
        items = (await session.execute(stmt)).scalars().all()
    return [_revision_to_dict(r) for r in items], total


async def approve_revision(revision_id: str, user_id: str = "") -> dict:
    """批准修订 → 应用到 SkillORM"""
    async with get_db_session() as session:
        rev = await session.get(SkillRevisionORM, revision_id)
        if rev is None:
            return {"approved": False, "reason": "revision_not_found"}
        if rev.status != "pending":
            return {"approved": False, "reason": "already_processed"}

        skill = await session.get(SkillORM, rev.skill_id)
        if skill is None:
            return {"approved": False, "reason": "skill_not_found"}

        new_def = rev.new_definition
        skill.input_schema = new_def.get("input_schema", skill.input_schema)
        skill.description = new_def.get("description", skill.description)
        if "source_ref" in new_def:
            skill.source_ref = new_def["source_ref"]
        skill.version = rev.version
        skill.changelog = [*skill.changelog, {
            "version": rev.version, "change": rev.diff_summary,
            "source": rev.source, "at": datetime.now().isoformat(),
        }]
        rev.status = "approved"
        rev.reviewed_by = user_id or None
        rev.reviewed_at = datetime.now()
        await session.flush()
        log.info("Revision approved: {} → skill {} v{}", revision_id, skill.id, rev.version)
        return {"approved": True, "skill_id": skill.id, "version": rev.version}


async def reject_revision(revision_id: str, user_id: str = "") -> dict:
    """拒绝修订"""
    async with get_db_session() as session:
        rev = await session.get(SkillRevisionORM, revision_id)
        if rev is None:
            return {"rejected": False, "reason": "revision_not_found"}
        rev.status = "rejected"
        rev.reviewed_by = user_id or None
        rev.reviewed_at = datetime.now()
        await session.flush()
        return {"rejected": True, "revision_id": revision_id}


# ============ 内部工具 ============

async def _create_draft(
    task_id: str, workflow_id: str, draft_data: dict, reusability: str,
) -> str:
    """持久化 Skill 草稿"""
    async with get_db_session() as session:
        draft = SkillDraftORM(
            name=draft_data.get("name", f"经验-{task_id[:8]}"),
            description=draft_data.get("description", "从成功任务沉淀"),
            input_schema={"type": "object", "properties": {}},
            source_type="workflow", source_ref=workflow_id,
            tags=draft_data.get("tags", ["经验沉淀"]),
            confidence=float(draft_data.get("confidence", 0.5)),
            reasoning=draft_data.get("reasoning", ""),
            origin="reflector", task_id=task_id, status="pending",
        )
        session.add(draft)
        await session.flush()
        log.info("Skill draft created: {} (task={} reusability={})", draft.id, task_id, reusability)
        return draft.id


def _build_success_trace(task: AgentTaskORM, steps: list) -> str:
    """构造成功轨迹摘要"""
    lines = [f"用户消息：{task.original_message or '(空)'}", "", "成功工具调用序列："]
    for i, s in enumerate(steps):
        args_str = json.dumps(s.tool_input, ensure_ascii=False)[:150] if s.tool_input else "{}"
        lines.append(f"{i+1}. 工具：{s.tool_name} 入参：{args_str}")
    return "\n".join(lines)


def _draft_to_dict(d: SkillDraftORM) -> dict:
    return {
        "id": d.id, "name": d.name, "description": d.description,
        "input_schema": d.input_schema, "source_type": d.source_type,
        "source_ref": d.source_ref, "tags": d.tags, "confidence": d.confidence,
        "reasoning": d.reasoning, "origin": d.origin, "task_id": d.task_id,
        "status": d.status, "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _revision_to_dict(r: SkillRevisionORM) -> dict:
    return {
        "id": r.id, "skill_id": r.skill_id, "version": r.version,
        "old_definition": r.old_definition, "new_definition": r.new_definition,
        "diff_summary": r.diff_summary, "source": r.source, "status": r.status,
        "confidence": r.confidence, "reasoning": r.reasoning,
        "created_by": r.created_by, "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
