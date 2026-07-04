"""会话重放路由 - 返回 Agent 任务的完整事件流（用于调试/审计）

GET /api/v1/agent/tasks/{task_id}/replay
返回：{task: {...}, events: [...], langfuse_url: "..."}
"""
from fastapi import APIRouter, Depends, Request

from app.route.depend import ok
from app.service.auth_service import CurrentUser, get_current_user

router = APIRouter()


@router.get("/tasks/{task_id}/replay", summary="会话重放")
async def replay_task(
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """返回 Agent 任务的完整事件流 + Langfuse trace URL（用于调试/审计）

    - admin 可查所有任务，普通用户仅查自己的
    - events 数组按时间排序，包含 step_started/llm_call/tool_call/step_completed 等
    - langfuse_url 指向 Langfuse UI 的 trace 详情页（若启用）
    """
    from app.service.replay_service import replay_service
    caller_uid = "" if user.role == "admin" else user.user_id
    result = await replay_service.get_replay(task_id, caller_uid)
    return ok(result, request)
