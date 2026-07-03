"""工作流路由 - 创建、查询、执行、状态"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.route.depend import ok, paginate
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


class WorkflowCreateRequest(BaseModel):
    """创建工作流"""
    name: str = Field(..., description="工作流名称")
    description: str = Field(default="", description="描述")
    definition: dict = Field(..., description="DAG 定义 {nodes, edges, variables}")
    trigger: dict = Field(default_factory=dict, description="触发器配置")
    enabled: bool = Field(default=True)


class WorkflowUpdateRequest(BaseModel):
    """更新工作流"""
    name: str | None = None
    description: str | None = None
    definition: dict | None = None
    trigger: dict | None = None
    enabled: bool | None = None


class WorkflowExecuteRequest(BaseModel):
    """执行工作流"""
    inputs: dict = Field(default_factory=dict, description="输入参数")
    chat_id: str = Field(default="", description="触发会话ID")
    user_id: str = Field(default="", description="触发用户ID")


@router.post("", status_code=201, summary="创建工作流")
async def create_workflow(
    body: WorkflowCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:manage")),
):
    from app.service.workflow_service import workflow_service
    return ok(await workflow_service.create_workflow(
        body.model_dump(), created_by=user.user_id
    ), request)


@router.get("", summary="工作流列表")
async def list_workflows(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    enabled: bool | None = None,
    keyword: str = "",
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.workflow_service import workflow_service
    items, total = await workflow_service.list_workflows(page, page_size, enabled, keyword)
    return ok(paginate([_workflow_dict(w) for w in items], total, page, page_size), request)


@router.get("/{workflow_id}", summary="工作流详情")
async def get_workflow(
    workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.workflow_service import workflow_service
    return ok(_workflow_dict(await workflow_service.get_workflow(workflow_id)), request)


@router.put("/{workflow_id}", summary="更新工作流")
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:manage")),
):
    from app.service.workflow_service import workflow_service
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    return ok(await workflow_service.update_workflow(workflow_id, update_data), request)


@router.delete("/{workflow_id}", summary="删除工作流")
async def delete_workflow(
    workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:manage")),
):
    from app.service.workflow_service import workflow_service
    return ok(await workflow_service.delete_workflow(workflow_id), request)


@router.post("/{workflow_id}/execute", status_code=202, summary="执行工作流")
async def execute_workflow(
    workflow_id: str,
    body: WorkflowExecuteRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:execute")),
):
    from app.service.workflow_service import workflow_service
    return ok(await workflow_service.execute_workflow(
        workflow_id=workflow_id,
        inputs=body.inputs,
        chat_id=body.chat_id,
        user_id=body.user_id or user.user_id,
    ), request)


@router.get("/executions/{execution_id}", summary="执行状态")
async def get_execution(
    execution_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.workflow_service import workflow_service
    return ok(await workflow_service.get_execution(execution_id), request)


def _workflow_dict(w) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "definition": w.definition,
        "trigger": w.trigger,
        "enabled": w.enabled,
        "version": w.version,
        "created_by": w.created_by,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }
