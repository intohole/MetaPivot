"""路由层共享依赖与工具

- 统一响应包装（success/error）
- 分页参数
- request_id 注入
"""
from typing import Any

from fastapi import Query, Request
from pydantic import BaseModel, Field

from app.utils.response import success_response


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量")


def page_params(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> PaginationParams:
    """分页查询参数依赖（统一声明，消除各路由重复定义）"""
    return PaginationParams(page=page, page_size=page_size)


def get_request_id(request: Request) -> str:
    """从请求状态获取 request_id"""
    return getattr(request.state, "request_id", "")


def ok(data: Any, request: Request) -> dict:
    """成功响应包装"""
    return success_response(data, request_id=get_request_id(request))


class PageResult(BaseModel):
    """分页响应"""
    items: list
    total: int
    page: int
    page_size: int


def paginate(items: list, total: int, page: int, page_size: int) -> dict:
    """构造分页响应数据"""
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
