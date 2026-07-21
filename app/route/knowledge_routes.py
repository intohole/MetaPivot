"""知识库路由 - 文档上传、查询、删除、检索"""
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.route.depend import ok, page_params, paginate, PaginationParams
from app.service.auth_service import CurrentUser, require_permission

router = APIRouter()


@router.post("/documents", status_code=201, summary="上传文档")
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="文档文件"),
    metadata: str = Form(default="{}", description="元数据 JSON 字符串"),
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    from app.service.knowledge_service import knowledge_service
    result = await knowledge_service.upload_document(file, metadata, user.user_id)
    return ok(result, request)


@router.get("/documents", summary="文档列表")
async def list_documents(
    request: Request,
    pg: PaginationParams = Depends(page_params),
    status: str = "",
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    from app.service.knowledge_service import knowledge_service
    items, total = await knowledge_service.list_documents(pg.page, pg.page_size, status, user.tenant_id)
    return ok(paginate(items, total, pg.page, pg.page_size), request)


@router.delete("/documents/{document_id}", summary="删除文档")
async def delete_document(
    document_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    from app.service.knowledge_service import knowledge_service
    return ok(await knowledge_service.delete_document(document_id), request)


@router.post("/search", summary="知识库检索")
async def search(
    body: dict,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    from app.service.knowledge_service import knowledge_service
    query = body.get("query", "")
    top_k = body.get("top_k", 5)
    score_threshold = body.get("score_threshold", 0.7)
    filter_ = body.get("filter", {})
    return ok(await knowledge_service.search(query, top_k, score_threshold, filter_), request)
