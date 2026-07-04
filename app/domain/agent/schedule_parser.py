"""定时任务解析器 - 从用户消息中解析调度意图

三层架构（Phase 5）：
1. L1 正则预筛（cron_regex.try_match）：覆盖约 70% 高频中文模式（"每天 9 点"等），
   命中即直接输出 cron_expr，避免 LLM 成本
2. L2 LLM 解析（SCHEDULE_PARSE_PROMPT）：L1 未命中时调用 LLM 精细解析，
   输出结构化 {run_at, recurring, cron_expr, task_message}
3. L3 关键词兜底（_keyword_parse）：LLM 失败时降级为关键词规则匹配

输出：ScheduleParseResult dataclass（含 cron_expr 优先于 recurring）
"""
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.domain.agent.cron_helper import is_valid_cron
from app.domain.agent.cron_regex import try_match as _regex_match
from app.domain.agent.prompts import SCHEDULE_PARSE_PROMPT
from app.utils.logger import get_logger

log = get_logger("schedule_parser")

# 触发定时解析的关键词
_SCHEDULE_KEYWORDS = (
    "明天", "后天", "下周", "每天", "每周", "每月", "每月",
    "提醒", "定时", "定时任务", "下午", "上午",
    "点", "点钟", "之后", "稍后", "later",
    "工作日", "周末", "每小时", "每分钟",
)


@dataclass
class ScheduleParseResult:
    """定时任务解析结果"""
    is_scheduled: bool = False
    run_at: Optional[datetime] = None
    recurring: str = "none"  # none/daily/weekly/monthly
    cron_expr: str = ""  # 标准 cron 表达式（优先于 recurring）
    task_message: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "is_scheduled": self.is_scheduled,
            "run_at": self.run_at.isoformat() if self.run_at else None,
            "recurring": self.recurring,
            "cron_expr": self.cron_expr,
            "task_message": self.task_message,
            "description": self.description,
        }


def has_schedule_intent(message: str) -> bool:
    """快速预筛：消息是否包含定时意图关键词

    在调用 L1/L2 之前用，避免每条消息都触发解析（成本控制）。
    """
    if not message or len(message) < 4:
        return False
    return any(kw in message for kw in _SCHEDULE_KEYWORDS)


async def parse_schedule(
    message: str, llm_provider: object,
) -> ScheduleParseResult:
    """解析消息中的定时任务意图

    三层架构：L1 正则 → L2 LLM → L3 关键词兜底

    Args:
        message: 用户原始消息
        llm_provider: LLM Provider 实例

    Returns:
        ScheduleParseResult — is_scheduled=false 表示无定时意图
    """
    if not has_schedule_intent(message):
        return ScheduleParseResult()

    # L1: 正则预筛（覆盖约 70% 高频场景，避免 LLM 成本）
    m = _regex_match(message)
    if m.matched and is_valid_cron(m.cron_expr):
        log.info("L1 regex matched: cron='{}' msg='{}'", m.cron_expr, m.task_message[:50])
        return ScheduleParseResult(
            is_scheduled=True,
            cron_expr=m.cron_expr,
            task_message=m.task_message,
            description=m.description,
        )

    # L2: LLM 解析
    try:
        result = await _llm_parse(message, llm_provider)
        if result is not None:
            # 校验 cron_expr 合法性（LLM 可能输出无效表达式）
            if result.cron_expr and not is_valid_cron(result.cron_expr):
                log.warning("LLM output invalid cron_expr={}, fallback to recurring", result.cron_expr)
                result.cron_expr = ""
            return result
    except Exception as e:
        log.warning("LLM parse_schedule failed: {}", e)

    # L3: 关键词兜底
    return _keyword_parse(message)


async def _llm_parse(message: str, llm_provider: object) -> Optional[ScheduleParseResult]:
    """调用 LLM 解析定时任务"""
    prompt = SCHEDULE_PARSE_PROMPT.format(
        now=datetime.now().isoformat(),
        message=message,
    )
    result = await llm_provider.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=200,
    )
    content = result.get("content", "").strip()
    parsed = json.loads(content)

    if not parsed.get("is_scheduled", False):
        return ScheduleParseResult()

    run_at_str = parsed.get("run_at")
    run_at = _parse_iso8601(run_at_str) if run_at_str else None
    recurring = parsed.get("recurring", "none")
    if recurring not in ("none", "daily", "weekly", "monthly"):
        recurring = "none"
    cron_expr = (parsed.get("cron_expr") or "").strip()
    # cron_expr 非空时 recurring 置 none（cron 优先）
    if cron_expr:
        recurring = "none"
    task_message = (parsed.get("task_message") or message).strip()
    description = (parsed.get("description") or "")[:50]

    log.info(
        "LLM parsed: run_at={} recurring={} cron='{}' msg='{}'",
        run_at, recurring, cron_expr, task_message[:50],
    )
    return ScheduleParseResult(
        is_scheduled=True,
        run_at=run_at,
        recurring=recurring,
        cron_expr=cron_expr,
        task_message=task_message,
        description=description,
    )


def _parse_iso8601(s: str) -> Optional[datetime]:
    """解析 ISO8601 时间字符串，容错多种格式"""
    if not s:
        return None
    # 尝试带时区
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    # 降级：去掉时区后缀重试
    try:
        # 截掉 +08:00 / Z 等时区标记
        clean = s.split("+")[0].rstrip("Z")
        return datetime.fromisoformat(clean)
    except ValueError:
        log.warning("Failed to parse ISO8601: {}", s)
        return None


def _keyword_parse(message: str) -> ScheduleParseResult:
    """关键词兜底解析（LLM 不可用时使用）

    支持的简单模式：
    - "明天" → run_at = 明天此时
    - "每天" → recurring=daily
    - "每周" → recurring=weekly
    """
    recurring = "none"
    if "每天" in message or "每日" in message:
        recurring = "daily"
    elif "每周" in message or "每周" in message:
        recurring = "weekly"
    elif "每月" in message:
        recurring = "monthly"

    run_at = None
    now = datetime.now()
    if "明天" in message:
        run_at = now.replace(day=now.day + 1) if now.day < 28 else now
    elif "后天" in message:
        run_at = now.replace(day=now.day + 2) if now.day < 27 else now

    if recurring == "none" and run_at is None:
        return ScheduleParseResult()

    # 简单 task_message：去掉时间关键词
    task_message = message
    for kw in _SCHEDULE_KEYWORDS:
        task_message = task_message.replace(kw, "")
    task_message = task_message.strip() or message

    return ScheduleParseResult(
        is_scheduled=True,
        run_at=run_at,
        recurring=recurring,
        task_message=task_message,
        description=f"keyword_parsed: {task_message[:30]}",
    )
