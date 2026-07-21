"""工作流路由 - 创建、查询、执行、状态"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.route.depend import ok, page_params, paginate, PaginationParams
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


# ============ 模板（SOP）路由 ============

class TemplateCreateRequest(BaseModel):
    """创建模板"""
    name: str = Field(..., description="模板名称")
    description: str = Field(default="", description="描述")
    category: str = Field(default="general", description="分类")
    definition: dict = Field(..., description="DAG 定义")
    trigger_template: dict = Field(default_factory=dict, description="触发器模板")
    input_schema: dict = Field(default_factory=dict, description="输入参数 schema")
    tags: list = Field(default_factory=list, description="标签")
    visibility: str = Field(default="public", description="可见性 public/private")


class TemplateInstantiateRequest(BaseModel):
    """实例化模板为工作流"""
    name: str = Field(default="", description="实例工作流名称（空则用模板名+实例）")
    trigger_overrides: dict = Field(default_factory=dict, description="触发器覆盖配置")


@router.get("/templates", summary="模板列表")
async def list_templates(
    request: Request,
    pg: PaginationParams = Depends(page_params),
    category: str = "",
    keyword: str = "",
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.template_service import template_service
    items, total = await template_service.list_templates(pg.page, pg.page_size, category, keyword)
    return ok(paginate([_template_dict(t) for t in items], total, pg.page, pg.page_size), request)


@router.get("/templates/{template_id}", summary="模板详情")
async def get_template(
    template_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.template_service import template_service
    return ok(_template_dict(await template_service.get_template(template_id)), request)


@router.post("/templates", status_code=201, summary="创建模板")
async def create_template(
    body: TemplateCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:manage")),
):
    from app.service.template_service import template_service
    return ok(await template_service.create_template(body.model_dump(), created_by=user.user_id), request)


@router.post("/templates/{template_id}/instantiate", status_code=201, summary="实例化模板为工作流")
async def instantiate_template(
    template_id: str,
    body: TemplateInstantiateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:execute")),
):
    from app.service.template_service import template_service
    return ok(await template_service.instantiate_template(
        template_id=template_id,
        name=body.name,
        trigger_overrides=body.trigger_overrides,
        user_id=user.user_id,
    ), request)


@router.delete("/templates/{template_id}", summary="删除模板")
async def delete_template(
    template_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:manage")),
):
    from app.service.template_service import template_service
    return ok(await template_service.delete_template(template_id), request)


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
    pg: PaginationParams = Depends(page_params),
    enabled: bool | None = None,
    keyword: str = "",
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    from app.service.workflow_service import workflow_service
    items, total = await workflow_service.list_workflows(pg.page, pg.page_size, enabled, keyword, user.tenant_id)
    return ok(paginate([_workflow_dict(w) for w in items], total, pg.page, pg.page_size), request)


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


class ResumeRequest(BaseModel):
    """工作流恢复请求（HITL 暂停后用户决策）"""
    decision: str = Field(..., description="决策: approve/reject/modify")
    modifications: dict | None = Field(default=None, description="修改参数（decision=modify 时用）")


@router.post("/executions/{execution_id}/resume", summary="恢复 HITL 暂停的工作流")
async def resume_workflow_execution(
    execution_id: str,
    body: ResumeRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:execute")),
):
    """恢复 HITL 暂停的工作流执行

    decision:
    - approve: 批准继续执行
    - reject: 拒绝并取消工作流
    - modify: 应用 modifications 后继续执行
    """
    from app.service.workflow_service import workflow_service
    return ok(await workflow_service.resume_execution(
        execution_id=execution_id,
        decision=body.decision,
        modifications=body.modifications or {},
    ), request)


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


def _template_dict(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category": t.category,
        "definition": t.definition,
        "trigger_template": t.trigger_template,
        "input_schema": t.input_schema,
        "tags": t.tags or [],
        "visibility": t.visibility,
        "created_by": t.created_by,
        "usage_count": t.usage_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
