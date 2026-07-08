"""定时任务 DLQ（死信队列）路由 - Phase 5

支持：
- GET  /schedules/dlq：查询失败任务列表（retry_count >= max_retries 进入 DLQ）
- POST /schedules/dlq/{task_id}/retry：手动重试（重置 retry_count=0，重新入队）
- POST /schedules/dlq/{task_id}/cancel：放弃任务（标 cancelled）

DLQ 设计：
- 任务执行失败时 retry_count += 1，若 < max_retries 则指数退避重试
- 若 retry_count >= max_retries（默认 3），状态变为 failed，进入 DLQ
- DLQ 任务可通过 retry 接口手动重新入队，或 cancel 放弃
"""
from fastapi import APIRouter, Depends, Query, Request

from app.route.depend import ok
from app.service.auth_service import CurrentUser, get_current_user
from app.utils.response import AppError, ErrorCode

router = APIRouter()


@router.get("", summary="查询死信任务列表")
async def list_dlq(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """查询失败任务列表（DLQ）

    返回 retry_count >= max_retries 的 failed 任务，按 updated_at 倒序。
    admin 用户可查所有，普通用户仅查自己的（由 list_dlq 内 user_id 过滤）。
    """
    from app.infra.scheduler.factory import get_scheduler
    scheduler = await get_scheduler()
    result = await scheduler.list_dlq(
        user_id=user.user_id, page=page, page_size=page_size,
    )
    return ok(result, request)


@router.post("/{task_id}/retry", summary="手动重试死信任务")
async def retry_dlq(
    task_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """手动重试（重置 retry_count=0，状态回 pending，next_run_at=now 立即入队）"""
    from app.infra.scheduler.factory import get_scheduler
    scheduler = await get_scheduler()
    success = await scheduler.retry_failed(task_id, user_id=user.user_id)
    if not success:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在或无权操作", 404)
    return ok({"task_id": task_id, "status": "pending"}, request)


@router.post("/{task_id}/cancel", summary="放弃死信任务")
async def cancel_dlq(
    task_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """放弃任务（标 cancelled，永久停止）"""
    from app.infra.scheduler.factory import get_scheduler
    scheduler = await get_scheduler()
    success = await scheduler.cancel(task_id)
    if not success:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在或已执行", 404)
    return ok({"task_id": task_id, "status": "cancelled"}, request)
