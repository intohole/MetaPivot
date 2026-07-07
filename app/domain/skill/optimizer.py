"""SkillOptimizer - Skill 自进化引擎

核心能力：基于执行反馈自动优化 Skill 定义。

流程：
  1. 收集 Skill 近期执行记录（SkillExecutionORM）
  2. 计算成功率/失败模式/平均耗时
  3. 失败率超阈值 → LLM 分析失败模式 + 生成优化建议
  4. 创建 SkillRevisionORM（pending/auto_merged）
  5. 高置信度(≥0.9)自动合并；否则等待人工 Review

设计原则：
  - 用低成本模型（temperature=0.1）做优化分析
  - 每日每 Skill 最多优化 1 次（防成本失控）
  - 所有变更可审计、可回滚（SkillRevisionORM + SkillORM.changelog）
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from app.infra.db.models_user_skill import (
    SkillExecutionORM, SkillORM, SkillRevisionORM,
)
from app.infra.db.session import get_db_session
from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger

log = get_logger("skill_optimizer")

# 触发优化的阈值
FAILURE_RATE_THRESHOLD = 0.3  # 失败率 > 30% 触发优化
MIN_EXECUTIONS_FOR_OPT = 3   # 至少 3 次执行才分析
AUTO_MERGE_CONFIDENCE = 0.9  # 置信度 ≥ 0.9 自动合并
OPTIMIZE_COOLDOWN_HOURS = 24  # 同一 Skill 24h 内不重复优化

OPTIMIZE_PROMPT = """你是 Skill 优化专家。给定一个 Skill 的当前定义和近期失败执行记录，分析失败模式并生成优化建议。

当前 Skill 定义：
{skill_def}

近期失败记录（最近 {fail_count} 次）：
{failures}

输出 JSON：
{{
  "failure_pattern": "失败模式总结（1句话）",
  "root_causes": ["原因1", "原因2"],
  "optimization_type": "schema_refine|description_clarify|param_default|error_handling",
  "optimized_input_schema": {{优化后的 input_schema}},
  "optimized_description": "优化后的 description",
  "diff_summary": "人类可读的变更摘要（1-2句）",
  "confidence": 0.0-1.0,
  "reasoning": "为什么这样优化"
}}

规则：
1. 只修改必要部分，不要大改 schema 结构
2. optimization_type=error_handling 时可在 description 中补充错误处理指引
3. confidence < 0.7 表示不确定，不要自动合并
4. 若失败都是外部原因（网络/API 不可用），optimization_type=description_clarify 补充说明即可"""


async def record_execution(
    skill_id: str, skill_name: str, status: str, duration_ms: int = 0,
    task_id: str = "", args_summary: Optional[dict] = None,
    error_message: str = "",
) -> None:
    """记录一次 Skill 执行结果（供 optimizer 分析）"""
    async with get_db_session() as session:
        record = SkillExecutionORM(
            skill_id=skill_id, skill_name=skill_name,
            task_id=task_id or None, status=status,
            duration_ms=duration_ms, args_summary=args_summary,
            error_message=error_message or None,
        )
        session.add(record)
        await session.flush()


async def check_and_optimize(skill_id: str) -> dict:
    """检查 Skill 是否需要优化，若需要则触发优化

    Returns: {optimized, reason, revision_id?, confidence?}
    """
    # 1. 检查冷却期：24h 内是否已优化过
    if await _in_cooldown(skill_id):
        return {"optimized": False, "reason": "in_cooldown"}

    # 2. 收集近期执行统计
    stats = await _get_execution_stats(skill_id)
    if stats["total"] < MIN_EXECUTIONS_FOR_OPT:
        return {"optimized": False, "reason": "insufficient_data", "executions": stats["total"]}

    if stats["failure_rate"] < FAILURE_RATE_THRESHOLD:
        return {"optimized": False, "reason": "healthy", "failure_rate": stats["failure_rate"]}

    # 3. 触发 LLM 优化
    log.info(
        "Optimizing skill {}: total={} fail_rate={:.0%}",
        skill_id, stats["total"], stats["failure_rate"],
    )
    revision_id = await _generate_optimization(skill_id, stats)
    if revision_id:
        return {"optimized": True, "revision_id": revision_id, "failure_rate": stats["failure_rate"]}
    return {"optimized": False, "reason": "optimization_failed"}


async def _get_execution_stats(skill_id: str) -> dict:
    """获取 Skill 近 7 天的执行统计"""
    since = datetime.now() - timedelta(days=7)
    async with get_db_session() as session:
        # 总执行数
        total_stmt = select(func.count()).where(
            SkillExecutionORM.skill_id == skill_id,
            SkillExecutionORM.created_at >= since,
        )
        total = (await session.execute(total_stmt)).scalar() or 0

        # 失败数
        fail_count_stmt = select(func.count()).where(
            SkillExecutionORM.skill_id == skill_id,
            SkillExecutionORM.status == "failed",
            SkillExecutionORM.created_at >= since,
        )
        failed = (await session.execute(fail_count_stmt)).scalar() or 0

        # 失败记录详情（取最近 5 条）
        fail_stmt = select(SkillExecutionORM).where(
            SkillExecutionORM.skill_id == skill_id,
            SkillExecutionORM.status == "failed",
            SkillExecutionORM.created_at >= since,
        ).order_by(SkillExecutionORM.created_at.desc()).limit(5)
        failures = (await session.execute(fail_stmt)).scalars().all()

    return {
        "total": total, "failed": failed,
        "failure_rate": failed / total if total > 0 else 0.0,
        "failures": failures,
    }


async def _in_cooldown(skill_id: str) -> bool:
    """检查是否在优化冷却期内"""
    since = datetime.now() - timedelta(hours=OPTIMIZE_COOLDOWN_HOURS)
    async with get_db_session() as session:
        stmt = select(SkillRevisionORM).where(
            SkillRevisionORM.skill_id == skill_id,
            SkillRevisionORM.created_at >= since,
        ).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _generate_optimization(skill_id: str, stats: dict) -> Optional[str]:
    """LLM 生成优化方案并创建 SkillRevisionORM"""
    # 加载 Skill 当前定义
    async with get_db_session() as session:
        skill = await session.get(SkillORM, skill_id)
        if skill is None:
            return None
        skill_def = {
            "name": skill.name, "description": skill.description,
            "input_schema": skill.input_schema,
            "source_type": skill.source_type, "source_ref": skill.source_ref,
        }
        next_version = skill.version + 1

    # 构造失败记录摘要
    failures_text = "\n".join([
        f"- {f.created_at.isoformat()}: {f.error_message or 'unknown'} (args: {json.dumps(f.args_summary, ensure_ascii=False)[:100] if f.args_summary else '{}'})"
        for f in stats["failures"]
    ]) or "（无详情）"

    prompt = OPTIMIZE_PROMPT.format(
        skill_def=json.dumps(skill_def, ensure_ascii=False, indent=2),
        fail_count=stats["failed"], failures=failures_text,
    )

    try:
        result = await llm_json_call(
            "", prompt, temperature=0.1, max_tokens=800,
        )
    except Exception as e:
        log.warning("LLM optimization failed for skill {}: {}", skill_id, e)
        return None

    confidence = float(result.get("confidence", 0.0))
    diff_summary = result.get("diff_summary", "LLM 优化")
    reasoning = result.get("reasoning", "")

    # 构造新定义
    new_def = {
        "input_schema": result.get("optimized_input_schema", skill_def["input_schema"]),
        "description": result.get("optimized_description", skill_def["description"]),
        "source_ref": skill_def["source_ref"],  # 通常不改 source_ref
    }

    # 高置信度自动合并；否则等待 Review
    auto_merge = confidence >= AUTO_MERGE_CONFIDENCE
    status = "auto_merged" if auto_merge else "pending"

    async with get_db_session() as session:
        revision = SkillRevisionORM(
            skill_id=skill_id, version=next_version,
            old_definition=skill_def, new_definition=new_def,
            diff_summary=diff_summary, source="auto_optimize",
            status=status, confidence=confidence,
            reasoning=reasoning, created_by="system",
        )
        session.add(revision)
        await session.flush()

        if auto_merge:
            # 应用优化到 SkillORM
            skill = await session.get(SkillORM, skill_id)
            if skill:
                skill.input_schema = new_def["input_schema"]
                skill.description = new_def["description"]
                skill.version = next_version
                skill.changelog = [*skill.changelog, {
                    "version": next_version, "change": diff_summary,
                    "source": "auto_optimize", "at": datetime.now().isoformat(),
                }]

        log.info(
            "Skill optimization: skill={} rev={} status={} confidence={:.2f}",
            skill_id, revision.id, status, confidence,
        )
        return revision.id


async def get_skill_health(skill_id: str) -> dict:
    """获取 Skill 健康度（成功率/失败率/近期趋势）"""
    stats = await _get_execution_stats(skill_id)
    return {
        "skill_id": skill_id,
        "total_executions": stats["total"],
        "failed_executions": stats["failed"],
        "failure_rate": round(stats["failure_rate"], 3),
        "health": "healthy" if stats["failure_rate"] < FAILURE_RATE_THRESHOLD
                  else "degraded" if stats["failure_rate"] < 0.6
                  else "critical",
    }
