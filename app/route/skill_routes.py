"""Skill 管理路由 - 注册、查询、启用/禁用、测试"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.route.depend import ok, paginate
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


class SkillCreateRequest(BaseModel):
    """创建 Skill"""
    name: str = Field(..., description="Skill 名称（唯一）")
    description: str = Field(..., description="Skill 描述")
    input_schema: dict = Field(..., description="输入 JSON Schema")
    source_type: str = Field(..., description="mcp/function/workflow")
    source_ref: str = Field(..., description="能力引用（函数路径/MCP tool 名/工作流 ID）")
    permission: str = Field(default="user", description="所需权限")
    require_confirm: bool = Field(default=False, description="是否需要人工确认")
    tags: list[str] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    """更新 Skill"""
    name: str | None = None
    description: str | None = None
    input_schema: dict | None = None
    permission: str | None = None
    require_confirm: bool | None = None
    tags: list[str] | None = None


class SkillTestRequest(BaseModel):
    """测试 Skill"""
    input: dict = Field(default_factory=dict, description="输入参数")


class SkillFromRequest(BaseModel):
    """从 Workflow/Task 创建 Skill"""
    name: str = Field(..., description="Skill 名称")
    description: str = Field(..., description="Skill 描述")
    tags: list[str] = Field(default_factory=list)


@router.post("", status_code=201, summary="创建 Skill")
async def create_skill(
    body: SkillCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.create_skill(body.model_dump(), owner_id=user.user_id), request)


@router.get("", summary="Skill 列表")
async def list_skills(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    enabled: bool | None = None,
    source_type: str | None = None,
    keyword: str = "",
    scope: str = Query("all", pattern="^(all|my|team)$"),
    user: CurrentUser = Depends(require_permission("skill:read")),
):
    from app.service.skill_service import skill_service
    items, total = await skill_service.list_skills(
        page, page_size, enabled, source_type, keyword,
        owner_id=user.user_id, scope=scope,
    )
    return ok(paginate([_skill_dict(s) for s in items], total, page, page_size), request)


@router.post("/from-workflow/{workflow_id}", status_code=201, summary="从 Workflow 创建 Skill")
async def create_skill_from_workflow(
    workflow_id: str,
    body: SkillFromRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.create_skill_from_workflow(
        workflow_id, body.name, body.description, owner_id=user.user_id, tags=body.tags), request)


@router.post("/from-task/{task_id}", status_code=201, summary="从 Agent 任务录制 Skill")
async def create_skill_from_task(
    task_id: str,
    body: SkillFromRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.record_task_to_skill(
        task_id, body.name, body.description, owner_id=user.user_id, tags=body.tags), request)


@router.post("/extract-from-task/{task_id}", summary="LLM 抽取 skill 草稿（不持久化）")
async def extract_skill_from_task(
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.domain.skill.extractor import extract_skill_from_task as _impl
    return ok(await _impl(task_id), request)


@router.get("/{skill_id}", summary="Skill 详情")
async def get_skill(
    skill_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:read")),
):
    from app.service.skill_service import skill_service
    skill = await skill_service.get_skill(skill_id)
    return ok(_skill_dict(skill), request)


@router.put("/{skill_id}", summary="更新 Skill")
async def update_skill(
    skill_id: str,
    body: SkillUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    return ok(await skill_service.update_skill(skill_id, update_data), request)


@router.delete("/{skill_id}", summary="删除 Skill")
async def delete_skill(
    skill_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.delete_skill(skill_id), request)


@router.post("/{skill_id}/enable", summary="启用 Skill")
async def enable_skill(
    skill_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.set_enabled(skill_id, True), request)


@router.post("/{skill_id}/disable", summary="禁用 Skill")
async def disable_skill(
    skill_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.set_enabled(skill_id, False), request)


@router.post("/{skill_id}/test", summary="测试 Skill")
async def test_skill(
    skill_id: str,
    body: SkillTestRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:read")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.test_skill(skill_id, body.input), request)


@router.post("/{skill_id}/publish", summary="发布到团队（private→shared）")
async def publish_skill(
    skill_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("skill:manage")),
):
    from app.service.skill_service import skill_service
    return ok(await skill_service.publish_to_team(skill_id, user.user_id), request)


def _skill_dict(s) -> dict:
    """ORM 转字典"""
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "input_schema": s.input_schema,
        "source_type": s.source_type,
        "source_ref": s.source_ref,
        "permission": s.permission,
        "require_confirm": s.require_confirm,
        "tags": s.tags,
        "enabled": s.enabled,
        "call_count": s.call_count,
        "last_called_at": s.last_called_at.isoformat() if s.last_called_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "owner_id": s.owner_id,
        "visibility": s.visibility,
        "version": s.version,
        "changelog": s.changelog,
    }
