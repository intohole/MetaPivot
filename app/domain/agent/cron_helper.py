"""croniter 封装 - 标准 cron 表达式校验与下次执行时间计算

将"每天 9 点"、"工作日 8:30"等场景统一为 cron 表达式，由 croniter 计算精确 next_run_at。
比 timedelta(days=1) 精确（跨月/闰年/cron 特性如 1-5 工作日）。

被调用方：
- schedule_parser._llm_parse：校验 LLM 输出的 cron_expr 合法性
- async_scheduler.schedule / _execute_one：计算首次/下次执行时间
"""
from datetime import datetime
from typing import Optional

from croniter import croniter

from app.utils.logger import get_logger

log = get_logger("cron_helper")


def is_valid_cron(expr: str) -> bool:
    """校验 cron 表达式是否合法

    支持 5 段标准 cron：分 时 日 月 周（如 "0 9 * * 1-5"）
    """
    if not expr or not isinstance(expr, str):
        return False
    try:
        croniter(expr, datetime.now())
        return True
    except Exception:
        return False


def next_run_at(expr: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """计算下次执行时间（base 缺省为 now）

    用于：
    - schedule() 创建任务时计算首次 next_run_at
    - _execute_one() 周期性任务执行后计算下次 next_run_at
    """
    if not expr:
        return None
    try:
        return croniter(expr, base or datetime.now()).get_next(datetime)
    except Exception as e:
        log.warning("croniter next_run_at failed expr={} err={}", expr, e)
        return None


def prev_run_at(expr: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """计算上次执行时间（用于回溯诊断，如"上次执行是什么时候"）"""
    if not expr:
        return None
    try:
        return croniter(expr, base or datetime.now()).get_prev(datetime)
    except Exception as e:
        log.warning("croniter prev_run_at failed expr={} err={}", expr, e)
        return None
