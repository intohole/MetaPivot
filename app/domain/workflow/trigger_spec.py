"""TriggerSpec - 工作流触发器配置解析与校验

支持三种触发方式：
- manual: 默认，通过 API 手动触发
- webhook: 外部系统 HTTP POST 触发（创建时由 webhook_service 自动生成 WebhookORM）
- schedule: cron 表达式定时触发（调 IScheduler.schedule）

设计：
- 轻量 dataclass，不依赖 Pydantic（domain 层不依赖 web 框架）
- parse_trigger 在 workflow_service.create/update 时调用，校验配置合法性
- webhook_token 创建后由 webhook_service 回填到 WorkflowORM.trigger
"""
from dataclasses import dataclass, field
from typing import Optional

from app.utils.response import AppError, ErrorCode

# 标准 cron 表达式段数：分 时 日 月 周
VALID_CRON_FIELDS = 5


@dataclass
class TriggerSpec:
    """触发器配置（运行时形态）"""
    type: str = "manual"  # manual / webhook / schedule
    cron_expr: Optional[str] = None        # schedule 类型
    webhook_token: Optional[str] = None    # webhook 类型（创建后回填）
    event_filter: dict = field(default_factory=dict)  # 预留：事件过滤

    def to_dict(self) -> dict:
        """序列化为可持久化的 dict（存入 WorkflowORM.trigger）"""
        d: dict = {"type": self.type}
        if self.cron_expr:
            d["cron_expr"] = self.cron_expr
        if self.webhook_token:
            d["webhook_token"] = self.webhook_token
        if self.event_filter:
            d["event_filter"] = self.event_filter
        return d


def parse_trigger(trigger_dict: Optional[dict]) -> TriggerSpec:
    """解析 + 校验 trigger 配置

    Args:
        trigger_dict: WorkflowORM.trigger 字段（JSON dict），可为空

    Returns:
        TriggerSpec 运行时形态

    Raises:
        AppError: 配置非法时（未知类型 / cron 段数错误）
    """
    if not trigger_dict:
        return TriggerSpec(type="manual")

    t_type = trigger_dict.get("type", "manual")
    if t_type not in ("manual", "webhook", "schedule"):
        raise AppError(ErrorCode.WORKFLOW_INVALID, f"未知触发类型: {t_type}", 400)

    spec = TriggerSpec(type=t_type)

    if t_type == "schedule":
        cron = trigger_dict.get("cron_expr", "")
        if not cron or len(cron.split()) != VALID_CRON_FIELDS:
            raise AppError(
                ErrorCode.WORKFLOW_INVALID,
                f"cron 表达式非法（需 {VALID_CRON_FIELDS} 段: 分 时 日 月 周）: {cron}",
                400,
            )
        spec.cron_expr = cron
    elif t_type == "webhook":
        # webhook_token 创建后由 webhook_service 回填，解析时允许为空
        spec.webhook_token = trigger_dict.get("webhook_token")

    spec.event_filter = trigger_dict.get("event_filter", {})
    return spec
