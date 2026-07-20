"""Agent 状态定义 - LangGraph StateGraph 状态对象

状态机：INTENT → PLANNING → EXECUTING → HITL(可选) → REFLECTING → REPLY
"""
from datetime import datetime
from enum import Enum
from typing import Optional

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
    SCHEDULE = "schedule"      # 解析出定时任务，创建调度
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
    confirm_user: Optional[str] = None
    status: str = "pending"
    duration_ms: Optional[int] = None
    # 链路可见性：拆分 LLM vs 工具耗时（性能瓶颈定位）
    llm_duration_ms: Optional[int] = None
    tool_duration_ms: Optional[int] = None
    # Token 用量持久化（prompt/completion/total_tokens，来自 LLM usage）
    token_usage: Optional[dict] = None
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
    # Phase B1: Re-planning 支持（计划偏差大时触发 update_plan，对齐 LangChain PlanAndExecute）
    plan_revision_count: int = 0  # 已 re-plan 次数（防震荡，max_revisions=2）
    original_plan: list[dict] = Field(default_factory=list)  # 原始 plan（用于对比偏差）

    # HITL
    pending_confirm: Optional[dict] = None
    confirm_decision: Optional[str] = None
    confirm_modifications: Optional[dict] = None

    # 输出
    final_answer: str = ""
    result: dict = Field(default_factory=dict)
    error: Optional[dict] = None

    # 链路可见性：Token 用量累计（LLM 成本追踪，落 AgentTaskORM.total_tokens）
    total_tokens: int = 0
    # 任务起始时间（计算 duration_ms，落 AgentTaskORM.started_at/finished_at/duration_ms）
    started_at: Optional[datetime] = None

    # Phase 1: 子代理支持（orchestrator-worker 模式）
    parent_task_id: Optional[str] = None  # 父任务 ID（子代理场景）
    depth: int = 0  # 递归深度（防子代理死锁，MAX_DEPTH=2）

    # Phase 4: 链路追踪（request_id/trace_id 跨任务传播）
    request_id: str = ""
    trace_id: str = ""

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
