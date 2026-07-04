"""定时任务节点 - 从 nodes.py 抽离以控制文件行数

仅在 AgentMode.SCHEDULE 模式下执行：从 state.context["schedule_result"] 读取
解析结果，调用 IScheduler 创建调度任务，返回用户可读回复。
失败不抛异常（写 error 但 COMPLETED），避免 agent 任务卡住。
"""
from datetime import datetime

from app.domain.agent.state import AgentState, AgentStatus
from app.utils.logger import get_logger

log = get_logger("agent_scheduler_node")


async def scheduler_node(state: AgentState) -> dict:
    """定时任务节点：调用 IScheduler 创建调度任务"""
    state.add_event("step_started", {"step": "scheduler"})
    sched_dict = state.context.get("schedule_result") or {}
    if not sched_dict.get("is_scheduled"):
        state.add_event("error", {"code": "NO_SCHEDULE_RESULT", "message": "无定时任务解析结果"})
        return {
            "status": AgentStatus.COMPLETED,
            "final_answer": "未能解析定时任务信息，请明确时间（如：明天下午3点提醒我开会）",
        }

    from app.infra.scheduler.factory import get_scheduler

    run_at_str = sched_dict.get("run_at")
    run_at = datetime.fromisoformat(run_at_str) if run_at_str else None
    recurring = sched_dict.get("recurring", "none")
    cron_expr = sched_dict.get("cron_expr", "")
    task_message = sched_dict.get("task_message", "")
    description = sched_dict.get("description", "")

    try:
        scheduler = await get_scheduler()
        task_id = await scheduler.schedule(
            message=task_message,
            run_at=run_at,
            recurring=recurring,
            cron_expr=cron_expr,
            chat_id=state.chat_id,
            user_id=state.user_id,
            channel=state.channel,
            context={"original_request": state.original_message},
            description=description,
        )
        # 构造用户可读的回复
        if cron_expr:
            time_desc = f"Cron: {cron_expr}"
        elif run_at:
            time_desc = run_at.strftime("%Y-%m-%d %H:%M")
        else:
            time_desc = f"周期({recurring})"
        answer = (
            f"已创建定时任务（ID: {task_id}）\n"
            f"执行时间：{time_desc}\n"
            f"任务内容：{task_message}"
        )
        if recurring != "none":
            answer += f"\n重复模式：{recurring}"
        state.add_event("step_completed", {"step": "scheduler", "result": {"task_id": task_id}})
        return {
            "status": AgentStatus.COMPLETED,
            "final_answer": answer,
            "result": {"scheduled_task_id": task_id, "schedule": sched_dict},
        }
    except Exception as e:
        log.exception("scheduler_node failed: {}", e)
        state.add_event("error", {"code": "SCHEDULE_ERROR", "message": str(e)})
        return {
            "status": AgentStatus.COMPLETED,
            "final_answer": f"创建定时任务失败：{e}",
            "error": {"code": "SCHEDULE_ERROR", "message": str(e)},
        }
