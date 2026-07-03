"""任务恢复 - 启动时扫描卡死的 Agent 任务

服务异常重启后，之前处于 executing/reflecting/intent/planning 状态的任务会永久卡死。
本模块在启动时扫描这些任务，标记为 failed，避免任务永久阻塞。

策略：
- 扫描 status IN ('pending','intent','planning','executing','reflecting','waiting_confirm')
- 批量更新为 failed，记录 error 为 "service restarted"
- waiting_confirm 特殊处理：保留为 cancelled（用户无法再确认）
"""
from sqlalchemy import update, select

from app.infra.db.models_core import AgentTaskORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger

log = get_logger("task_recovery")

# 需要恢复的任务状态（这些状态意味着任务未正常完成）
_STUCK_STATUSES = ("pending", "intent", "planning", "executing", "reflecting", "waiting_confirm")

# 恢复后的目标状态
_RECOVERY_ERROR = {"code": "SERVICE_RESTARTED", "message": "服务重启，任务被中断"}


async def recover_stuck_tasks() -> int:
    """扫描并恢复卡死的任务，返回恢复数量"""
    recovered = 0
    async with get_db_session() as session:
        # 查询卡死的任务
        stmt = select(AgentTaskORM.id, AgentTaskORM.status).where(
            AgentTaskORM.status.in_(_STUCK_STATUSES)
        )
        stuck = (await session.execute(stmt)).all()
        if not stuck:
            return 0

        for task_id, old_status in stuck:
            # waiting_confirm → cancelled（用户无法再确认，标记为取消）
            new_status = "cancelled" if old_status == "waiting_confirm" else "failed"
            await session.execute(
                update(AgentTaskORM)
                .where(AgentTaskORM.id == task_id)
                .values(status=new_status, error=_RECOVERY_ERROR)
            )
            recovered += 1
            log.warning(
                "Recovered stuck task: id={} {} → {}",
                task_id, old_status, new_status,
            )

    if recovered:
        log.info("Recovered {} stuck tasks on startup", recovered)
    return recovered
