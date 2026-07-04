"""定时任务路由 - 创建/查询/取消定时任务

支持：
- POST /schedules：手动创建定时任务（除 Agent 自动解析外）
- GET /schedules：查询待执行任务列表
- GET /schedules/{id}：查询单个任务详情
- DELETE /schedules/{id}：取消未执行的任务
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.domain.contracts.scheduler import IScheduler
from app.infra.scheduler.factory import get_scheduler
from app.route.depend import ok
from app.service.auth_service import CurrentUser, get_current_user
from app.utils.response import AppError, ErrorCode

router = APIRouter()


class CreateScheduleRequest(BaseModel):
    """手动创建定时任务请求"""
    message: str = Field(..., min_length=1, max_length=10000, description="触发时执行的消息")
    run_at: Optional[str] = Field(None, description="ISO8601 时间（一次性任务）")
    recurring: str = Field(default="none", description="none/daily/weekly/monthly（cron_expr 为空时使用）")
    cron_expr: str = Field(default="", description="标准 5 段 cron（如 '0 9 * * 1-5' 工作日 9 点，优先于 recurring）")
    chat_id: str = Field(default="", description="触发源会话 ID（用于回调）")
    description: str = Field(default="", description="任务描述")


async def _get_scheduler() -> IScheduler:
    """获取 scheduler 实例（路由依赖）"""
    return await get_scheduler()


@router.post("", status_code=201, summary="创建定时任务")
async def create_schedule(
    body: CreateScheduleRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """手动创建定时任务（除 Agent 自动解析外）"""
    scheduler = await _get_scheduler()
    run_at = datetime.fromisoformat(body.run_at) if body.run_at else None
    task_id = await scheduler.schedule(
        message=body.message,
        run_at=run_at,
        recurring=body.recurring,
        cron_expr=body.cron_expr,
        chat_id=body.chat_id,
        user_id=user.user_id,
        channel="api",
        description=body.description,
    )
    return ok({"task_id": task_id, "status": "pending"}, request)


@router.get("", summary="查询定时任务列表")
async def list_schedules(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """查询待执行的定时任务"""
    scheduler = await _get_scheduler()
    items = await scheduler.list_pending(user_id=user.user_id, limit=page_size)
    return ok({"items": items, "total": len(items), "page": page, "page_size": page_size}, request)


@router.get("/{task_id}", summary="查询单个定时任务")
async def get_schedule(
    task_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """查询单个定时任务详情"""
    scheduler = await _get_scheduler()
    pending = await scheduler.list_pending(user_id=user.user_id, limit=1000)
    item = next((t for t in pending if t.get("id") == task_id), None)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "定时任务不存在或已执行", 404)
    return ok(item, request)


@router.delete("/{task_id}", summary="取消定时任务")
async def cancel_schedule(
    task_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """取消未执行的定时任务"""
    scheduler = await _get_scheduler()
    success = await scheduler.cancel(task_id)
    if not success:
        raise AppError(ErrorCode.VALIDATION_ERROR, "任务不存在或已执行", 400)
    return ok({"task_id": task_id, "status": "cancelled"}, request)
