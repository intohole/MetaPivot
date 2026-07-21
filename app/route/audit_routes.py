"""审计路由 - 日志查询与统计（仅 admin）"""
from fastapi import APIRouter, Depends, Query, Request

from app.route.depend import ok, page_params, paginate, PaginationParams
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


@router.get("/logs", summary="审计日志查询")
async def list_logs(
    request: Request,
    pg: PaginationParams = Depends(page_params),
    user_id: str = "",
    action: str = "",
    skill_id: str = "",
    start_time: str = "",
    end_time: str = "",
    user: CurrentUser = Depends(require_permission("audit:read")),
):
    from app.service.audit_service import audit_service
    items, total = await audit_service.list_logs(
        pg.page, pg.page_size, user_id, action, skill_id, start_time, end_time, user.tenant_id
    )
    return ok(paginate(items, total, pg.page, pg.page_size), request)


@router.get("/stats", summary="审计统计")
async def stats(
    request: Request,
    start_time: str = "",
    end_time: str = "",
    group_by: str = Query("day", pattern="^(day|user|skill)$"),
    user: CurrentUser = Depends(require_permission("audit:read")),
):
    from app.service.audit_service import audit_service
    return ok(await audit_service.stats(start_time, end_time, group_by), request)
