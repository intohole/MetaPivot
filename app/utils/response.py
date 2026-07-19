"""统一响应格式与错误码"""
from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """统一错误响应"""
    success: bool = Field(default=False, description="是否成功")
    error: dict = Field(description="错误信息")
    request_id: str = Field(description="请求追踪ID")


class SuccessResponse(BaseModel):
    """统一成功响应"""
    success: bool = Field(default=True, description="是否成功")
    data: Any = Field(description="响应数据")
    request_id: str = Field(description="请求追踪ID")


class ErrorCode:
    """错误码常量"""
    # 认证类
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_PERMISSION_DENIED = "AUTH_PERMISSION_DENIED"

    # Agent类
    AGENT_MAX_STEPS = "AGENT_MAX_STEPS"
    AGENT_HUMAN_REJECTED = "AGENT_HUMAN_REJECTED"
    AGENT_TASK_NOT_FOUND = "AGENT_TASK_NOT_FOUND"
    AGENT_TASK_CANCELLED = "AGENT_TASK_CANCELLED"

    # Skill类
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_DISABLED = "SKILL_DISABLED"
    SKILL_EXECUTION_FAILED = "SKILL_EXECUTION_FAILED"

    # LLM类
    LLM_RESPONSE_INVALID = "LLM_RESPONSE_INVALID"

    # 工作流类
    WORKFLOW_INVALID = "WORKFLOW_INVALID"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"

    # IM类
    IM_CHANNEL_ERROR = "IM_CHANNEL_ERROR"
    IM_SIGNATURE_INVALID = "IM_SIGNATURE_INVALID"

    # HTTP 类（Sprint 9.1: http_request 节点 + Skill source_type=http）
    HTTP_REQUEST_FAILED = "HTTP_REQUEST_FAILED"

    # 通用
    RATE_LIMITED = "RATE_LIMITED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_MESSAGES = {
    ErrorCode.AUTH_INVALID_CREDENTIALS: "用户名或密码错误",
    ErrorCode.AUTH_TOKEN_EXPIRED: "令牌已过期",
    ErrorCode.AUTH_PERMISSION_DENIED: "权限不足",
    ErrorCode.AGENT_MAX_STEPS: "Agent达到最大步数限制",
    ErrorCode.AGENT_HUMAN_REJECTED: "人工确认被拒绝",
    ErrorCode.AGENT_TASK_NOT_FOUND: "Agent任务不存在",
    ErrorCode.AGENT_TASK_CANCELLED: "Agent任务已取消",
    ErrorCode.SKILL_NOT_FOUND: "Skill不存在",
    ErrorCode.SKILL_DISABLED: "Skill已禁用",
    ErrorCode.SKILL_EXECUTION_FAILED: "Skill执行失败",
    ErrorCode.WORKFLOW_INVALID: "工作流定义无效",
    ErrorCode.WORKFLOW_NOT_FOUND: "工作流不存在",
    ErrorCode.IM_CHANNEL_ERROR: "IM渠道错误",
    ErrorCode.IM_SIGNATURE_INVALID: "IM签名校验失败",
    ErrorCode.HTTP_REQUEST_FAILED: "HTTP请求失败",
    ErrorCode.RATE_LIMITED: "请求频率超限",
    ErrorCode.RESOURCE_NOT_FOUND: "资源不存在",
    ErrorCode.VALIDATION_ERROR: "参数校验失败",
    ErrorCode.INTERNAL_ERROR: "内部错误",
}


class AppError(Exception):
    """业务异常基类"""

    def __init__(
        self,
        code: str = ErrorCode.INTERNAL_ERROR,
        message: Optional[str] = None,
        status_code: int = 500,
        details: Optional[dict] = None,
    ):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "未知错误")
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


def success_response(data: Any, request_id: str = "") -> dict:
    """构建成功响应"""
    return {"success": True, "data": data, "request_id": request_id}


def error_response(code: str, message: Optional[str] = None, request_id: str = "", details: Optional[dict] = None) -> dict:
    """构建错误响应"""
    err = {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "未知错误"),
    }
    if details:
        err["details"] = details
    return {"success": False, "error": err, "request_id": request_id}
