"""Agent 路由 - 超级 Agent 对话、任务管理、HITL 确认

P2 阶段先骨架化，待 AgentService 完成后填充实现。
"""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.route.depend import ok, paginate
from app.service.auth_service import CurrentUser, get_current_user

router = APIRouter()


class ChatRequest(BaseModel):
    """Agent 对话请求"""
    message: str = Field(..., min_length=1, max_length=10000, description="用户消息")
    channel: str = Field(default="api", description="消息来源渠道")
    chat_id: str = Field(default="", description="会话ID")
    user_id: str = Field(default="", description="调用方用户ID（IM场景）")
    context: dict = Field(default_factory=dict, description="附加上下文")
    stream: bool = Field(default=False, description="是否流式返回")


class ConfirmRequest(BaseModel):
    """HITL 确认请求"""
    decision: str = Field(..., description="approve/reject/modify")
    modifications: dict = Field(default_factory=dict, description="decision=modify时必填")


class MemorySearchRequest(BaseModel):
    """语义记忆检索请求（memory_backend=semantic 时生效）"""
    query: str = Field(..., min_length=1, max_length=2000, description="检索 query")
    chat_id: str = Field(default="", description="限定会话；留空跨所有会话语义检索")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条数")


@router.post("/chat", status_code=202, summary="发起 Agent 对话")
async def chat(
    body: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """发起 Agent 任务，返回 task_id 异步处理；stream=true 时返回 SSE"""
    from app.service.agent_service import agent_service
    result = await agent_service.start_task(
        message=body.message,
        channel=body.channel,
        chat_id=body.chat_id or f"api_{user.user_id}",
        user_id=body.user_id or user.user_id,
        context=body.context,
        stream=body.stream,
    )
    if body.stream:
        return EventSourceResponse(agent_service.stream_task(result["task_id"]))
    return ok(result, request)


@router.get("/tasks", summary="任务列表")
async def list_tasks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = "",
    user: CurrentUser = Depends(get_current_user),
):
    """查询 Agent 任务列表（admin 可查所有，普通用户仅查自己的）"""
    from app.service.agent_service import agent_service
    caller_uid = "" if user.role == "admin" else user.user_id
    items, total = await agent_service.list_tasks(page, page_size, caller_uid, status)
    return ok(paginate(items, total, page, page_size), request)


@router.get("/tasks/{task_id}", summary="查询任务状态")
async def get_task(
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """查询 Agent 任务状态、结果、步骤（仅任务发起人或 admin）"""
    from app.service.agent_service import agent_service
    # admin 可查所有任务，普通用户仅查自己的
    caller_uid = "" if user.role == "admin" else user.user_id
    return ok(await agent_service.get_task(task_id, caller_uid), request)


@router.get("/tasks/{task_id}/stream", summary="任务流式订阅")
async def stream_task(
    task_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """SSE 订阅任务步骤事件（仅任务发起人或 admin）"""
    from app.service.agent_service import agent_service
    caller_uid = "" if user.role == "admin" else user.user_id
    return EventSourceResponse(agent_service.stream_task(task_id, caller_uid))


@router.post("/tasks/{task_id}/confirm", summary="人工确认")
async def confirm_task(
    task_id: str,
    body: ConfirmRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """HITL 确认/拒绝/修改"""
    from app.service.agent_service import agent_service
    result = await agent_service.confirm_task(
        task_id=task_id,
        decision=body.decision,
        modifications=body.modifications,
        user_id=user.user_id,
    )
    return ok(result, request)


@router.post("/tasks/{task_id}/cancel", summary="取消任务")
async def cancel_task(
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """取消进行中的 Agent 任务"""
    from app.service.agent_service import agent_service
    return ok(await agent_service.cancel_task(task_id, user.user_id), request)


@router.post("/memory/search", summary="语义记忆检索")
async def search_memory(
    body: MemorySearchRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """跨会话语义记忆检索（memory_backend=semantic 时返回向量召回结果，其他 backend 返回空）

    用于查询用户偏好/历史事实，如"用户喜欢什么主题"。
    """
    from app.service.agent_service import agent_service
    return ok(await agent_service.search_memory(body.query, body.chat_id, body.top_k), request)
