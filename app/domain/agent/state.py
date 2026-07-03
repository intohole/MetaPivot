"""Agent 状态定义 - LangGraph StateGraph 状态对象

状态机：INTENT → PLANNING → EXECUTING → HITL(可选) → REFLECTING → REPLY
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Agent 任务状态枚举"""
    PENDING = "pending"
    INTENT = "intent"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_CONFIRM = "waiting_confirm"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentMode(str, Enum):
    """执行模式（由意图分类决定）"""
    PIPELINE = "pipeline"      # 简单问答，直接 LLM 回答
    AGENT = "agent"            # 需要工具调用的多步 Agent
    WORKFLOW = "workflow"      # 路由到指定工作流
    FALLBACK = "fallback"      # 兜底降级


class StepRecord(BaseModel):
    """单步执行记录"""
    step_index: int
    step_name: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[dict] = None
    require_confirm: bool = False
    confirm_decision: Optional[str] = None
    status: str = "pending"
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class AgentState(BaseModel):
    """LangGraph 节点间传递的状态对象

    注意：LangGraph 要求 state 可序列化，所有字段必须是 JSON 兼容类型。
    """

    # 任务标识
    task_id: str = ""
    user_id: str = ""
    channel: str = "api"
    chat_id: str = ""

    # 输入
    original_message: str = ""
    context: dict = Field(default_factory=dict)

    # 意图与模式
    intent: str = ""
    mode: AgentMode = AgentMode.AGENT

    # 执行状态
    status: AgentStatus = AgentStatus.PENDING
    current_step: int = 0
    max_steps: int = 10
    steps: list[StepRecord] = Field(default_factory=list)

    # LLM 对话历史
    messages: list[dict] = Field(default_factory=list)

    # 工具调用
    plan: list[dict] = Field(default_factory=list, description="规划步骤列表")
    available_tools: list[dict] = Field(default_factory=list, description="可用工具列表（OpenAI格式）")

    # HITL
    pending_confirm: Optional[dict] = None
    confirm_decision: Optional[str] = None
    confirm_modifications: Optional[dict] = None

    # 输出
    final_answer: str = ""
    result: dict = Field(default_factory=dict)
    error: Optional[dict] = None

    # 流式事件队列（仅运行时使用，不持久化）
    events: list[dict] = Field(default_factory=list, description="待推送的 SSE 事件")

    def add_event(self, event_type: str, data: dict) -> None:
        """记录流式事件"""
        self.events.append({"type": event_type, "data": data})

    def to_db_dict(self) -> dict:
        """转换为 DB 持久化字段（排除非持久化字段）"""
        return {
            "user_id": self.user_id or None,
            "channel": self.channel,
            "chat_id": self.chat_id or None,
            "original_message": self.original_message,
            "intent": self.intent,
            "mode": self.mode.value,
            "status": self.status.value,
            "plan": self.plan,
            "current_step": self.current_step,
            "result": self.result or None,
            "error": self.error,
        }
