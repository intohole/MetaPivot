"""Skill 自进化 Review 路由 - 草稿审批 + 修订审批

Sprint 8.2: 从 skill_routes.py 拆离，保持 skill_routes.py ≤ 300 行。
挂载到 /api/v1/skills（与 skill_routes 同前缀，路由合并）。
"""
from fastapi import APIRouter, Depends, Query, Request

from app.route.depend import ok, page_params, paginate, PaginationParams
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


# ============ Skill 自进化：草稿 Review ============

@router.get("/drafts/list", summary="Skill 草稿列表（待审核）")
async def list_drafts(
    request: Request,
    status: str = Query("pending", pattern="^(pending|approved|rejected)$"),
    pg: PaginationParams = Depends(page_params),
    user: CurrentUser = Depends(require_permission("skill:read")),
):
    from app.domain.skill.evolution import list_drafts as _impl
    items, total = await _impl(
        status=status, owner_id="", page=pg.page, page_size=pg.page_size, tenant_id=user.tenant_id)
    return ok(paginate(items, total, pg.page, pg.page_size), request)


@router.post("/drafts/{draft_id}/approve", summary="批准草稿 → 转为正式 Skill")
async def approve_draft(
    draft_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.domain.skill.evolution import approve_draft as _impl
    return ok(await _impl(draft_id, user_id=user.user_id, tenant_id=user.tenant_id), request)


@router.post("/drafts/{draft_id}/reject", summary="拒绝草稿")
async def reject_draft(
    draft_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.domain.skill.evolution import reject_draft as _impl
    return ok(await _impl(draft_id, user_id=user.user_id, tenant_id=user.tenant_id), request)


# ============ Skill 自进化：修订 Review ============

@router.get("/revisions/list", summary="Skill 修订列表（PR-like Review）")
async def list_revisions(
    request: Request,
    skill_id: str = "",
    status: str = Query("", pattern="^(|pending|approved|rejected|auto_merged)$"),
    pg: PaginationParams = Depends(page_params),
    user: CurrentUser = Depends(require_permission("skill:read")),
):
    from app.domain.skill.evolution import list_revisions as _impl
    items, total = await _impl(
        skill_id=skill_id, status=status, page=pg.page, page_size=pg.page_size,
        tenant_id=user.tenant_id)
    return ok(paginate(items, total, pg.page, pg.page_size), request)


@router.post("/revisions/{revision_id}/approve", summary="批准修订 → 应用到 Skill")
async def approve_revision(
    revision_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.domain.skill.evolution import approve_revision as _impl
    return ok(await _impl(revision_id, user_id=user.user_id, tenant_id=user.tenant_id), request)


@router.post("/revisions/{revision_id}/reject", summary="拒绝修订")
async def reject_revision(
    revision_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.domain.skill.evolution import reject_revision as _impl
    return ok(await _impl(revision_id, user_id=user.user_id, tenant_id=user.tenant_id), request)
