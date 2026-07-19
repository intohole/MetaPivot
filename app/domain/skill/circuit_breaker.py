"""Skill 熔断器 — 执行统计 + 失败率监控 + 自动熔断

Sprint 8.1: 从 optimizer.py 拆离，保持 optimizer.py ≤ 300 行。
职责：
- _get_execution_stats: 获取 Skill 近 window_hours 小时的执行统计
- check_and_circuit_break: 失败率过高自动禁用 Skill
- _safe_circuit_break_check: fire-and-forget 包装（吞异常）

依赖方向：circuit_breaker 独立无上层依赖；optimizer 反向 import 本模块。
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.infra.db.models_user_skill import SkillExecutionORM, SkillORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("skill_circuit_breaker")

# 自动熔断阈值（circuit breaker）：失败率过高自动禁用，防止坏技能持续损害体验
CIRCUIT_BREAK_FAILURE_RATE = 0.6  # 失败率 ≥ 60% 触发熔断
CIRCUIT_BREAK_MIN_EXECUTIONS = 5  # 至少 5 次执行才熔断（避免样本过小误判）
CIRCUIT_BREAK_WINDOW_HOURS = 24   # 统计窗口（近 24h）


async def get_execution_stats(skill_id: str, window_hours: int = 168) -> dict:
    """获取 Skill 近 window_hours 小时的执行统计（默认 7 天）

    Sprint 8.1: 从 optimizer._get_execution_stats 迁移为公开函数。
    """
    since = datetime.now() - timedelta(hours=window_hours)
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


async def safe_circuit_break_check(skill_id: str, skill_name: str) -> None:
    """包装熔断检查，吞异常避免 fire-and-forget 任务静默崩溃

    Sprint 8.1: 从 optimizer._safe_circuit_break_check 迁移为公开函数。
    """
    try:
        await check_and_circuit_break(skill_id, skill_name)
    except Exception as e:
        log.warning("circuit break check failed for skill {}: {}", skill_id, e)


async def check_and_circuit_break(skill_id: str, skill_name: str = "") -> dict:
    """检查 Skill 是否应熔断：近 24h 失败率 ≥ 60% 且 ≥ 5 次执行 → 自动禁用

    熔断后在 changelog 追加 {source: "circuit_breaker"} 标记，前端据此显示熔断 badge。
    Returns: {tripped, reason, failure_rate?, total?}
    """
    stats = await get_execution_stats(
        skill_id, window_hours=CIRCUIT_BREAK_WINDOW_HOURS,
    )
    if stats["total"] < CIRCUIT_BREAK_MIN_EXECUTIONS:
        return {"tripped": False, "reason": "insufficient_data", "total": stats["total"]}
    if stats["failure_rate"] < CIRCUIT_BREAK_FAILURE_RATE:
        return {"tripped": False, "reason": "healthy", "failure_rate": stats["failure_rate"]}

    # 触发熔断
    async with get_db_session() as session:
        skill = await session.get(SkillORM, skill_id)
        if skill is None:
            return {"tripped": False, "reason": "skill_not_found"}
        if not skill.enabled:
            # 已禁用（可能是手动禁用或已熔断），不重复操作
            return {"tripped": False, "reason": "already_disabled"}
        skill.enabled = False
        skill.changelog = [*skill.changelog, {
            "version": skill.version,
            "change": f"自动熔断：近{CIRCUIT_BREAK_WINDOW_HOURS}h失败率 {stats['failure_rate']:.0%}（{stats['failed']}/{stats['total']}）",
            "source": "circuit_breaker", "at": datetime.now().isoformat(),
            "failure_rate": round(stats["failure_rate"], 3),
        }]
        await session.flush()
        log.warning(
            "Skill circuit-break tripped: {} ({}) fail_rate={:.0%} ({}/{})",
            skill_id, skill_name or skill.name, stats["failure_rate"], stats["failed"], stats["total"],
        )
        return {
            "tripped": True, "reason": "failure_rate_exceeded",
            "failure_rate": stats["failure_rate"], "total": stats["total"],
        }
