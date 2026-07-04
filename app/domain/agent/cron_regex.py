"""NL→cron L1 正则预筛 - 中文常见时间模式转 cron 表达式

策略：先用关键词 + 正则匹配常见中文时间表达，命中即直接输出 cron_expr，
未命中再走 L2 LLM 解析（schedule_parser._llm_parse）。
覆盖约 70% 高频场景，节省 LLM 调用成本。

支持模式（示例 → cron）：
- "每天 9 点" / "每日 9:00"            → "0 9 * * *"
- "工作日 8:30" / "工作日早上 8 点"     → "0 8 * * 1-5"
- "周末 10 点"                          → "0 10 * * 0,6"
- "每周一 9 点" / "每周一上午 9 点"      → "0 9 * * 1"
- "每月 1 号 9 点" / "每月 1 日 9:00"    → "0 9 1 * *"
- "每小时"                              → "0 * * * *"
- "每 30 分钟"                          → "*/30 * * * *"
- "每 2 小时"                           → "0 */2 * * *"
"""
import re
from dataclasses import dataclass
from typing import Tuple

_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_WEEKDAY_CN = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 0, "天": 0,
}

# 时间关键词（用于 _strip_time 清理任务主体）
_TIME_KEYWORDS = (
    "每天|每日|日常|工作日|平日|周末|周六日|双休日|"
    "每周[一二三四五六日天]?|每月\\d{1,2}[号日]?|"
    "每\\s*\\d+\\s*分钟|每\\s*\\d+\\s*小时|每小时|"
    "\\d{1,2}\\s*[:：点]\\s*\\d{0,2}|\\d{1,2}\\s*点钟|"
    "上午|下午|早上|晚上|清晨|凌晨|点|点钟|时"
)


@dataclass
class CronMatch:
    """L1 正则匹配结果"""
    matched: bool = False
    cron_expr: str = ""
    task_message: str = ""
    description: str = ""


def _to_int(s: str) -> int:
    """中文/阿拉伯数字转 int"""
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s, 0)


def _parse_hhmm(text: str) -> Tuple[int, int]:
    """从文本中提取 HH:MM，默认 09:00

    支持：
    - "9:30" / "9：30" / "09:00"
    - "9点30" / "9点" / "9点钟"
    - 上午/下午 修饰（下午 3 点 → 15:00）
    """
    # 提取上午/下午修饰
    hour_offset = 0
    if re.search(r"下午|傍晚", text):
        hour_offset = 12
    # 优先匹配 HH:MM 或 HH点MM
    m = re.search(r"(\d{1,2})\s*[:：点]\s*(\d{0,2})", text)
    if m:
        h = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        # 应用上午/下午修饰
        if hour_offset and h < 12:
            h += hour_offset
        if 0 <= h <= 23 and 0 <= mm <= 59:
            return h, mm
    # 仅小时："下午 3 点" / "晚上 8 点"
    m = re.search(r"(\d{1,2})\s*点", text)
    if m:
        h = int(m.group(1))
        if hour_offset and h < 12:
            h += hour_offset
        if 0 <= h <= 23:
            return h, 0
    return 9, 0


def try_match(message: str) -> CronMatch:
    """尝试匹配常见中文 cron 模式（L1 预筛）

    Returns:
        CronMatch - matched=True 表示命中，调用方直接用 cron_expr
    """
    if not message:
        return CronMatch()
    msg = message.strip()

    # 工作日 + 时间（高频场景，优先匹配）
    if re.search(r"工作日|平日", msg):
        h, mm = _parse_hhmm(msg)
        cron = f"{mm} {h} * * 1-5"
        return CronMatch(True, cron, _strip_time(msg), f"工作日 {h:02d}:{mm:02d}")

    # 周末
    if re.search(r"周末|周六日|双休日", msg):
        h, mm = _parse_hhmm(msg)
        cron = f"{mm} {h} * * 0,6"
        return CronMatch(True, cron, _strip_time(msg), f"周末 {h:02d}:{mm:02d}")

    # 每周X
    m = re.search(r"每周([一二三四五六日天])", msg)
    if m:
        w = _WEEKDAY_CN.get(m.group(1), 1)
        h, mm = _parse_hhmm(msg)
        cron = f"{mm} {h} * * {w}"
        return CronMatch(True, cron, _strip_time(msg), f"每周{m.group(1)} {h:02d}:{mm:02d}")

    # 每月X号/日
    m = re.search(r"每月(\d{1,2}|[一二三四五六七八九十])[号日]", msg)
    if m:
        day = _to_int(m.group(1))
        if 1 <= day <= 31:
            h, mm = _parse_hhmm(msg)
            cron = f"{mm} {h} {day} * *"
            return CronMatch(True, cron, _strip_time(msg), f"每月{day}号 {h:02d}:{mm:02d}")

    # 每天/每日
    if re.search(r"每天|每日|日常", msg):
        h, mm = _parse_hhmm(msg)
        cron = f"{mm} {h} * * *"
        return CronMatch(True, cron, _strip_time(msg), f"每天 {h:02d}:{mm:02d}")

    # 每小时
    if re.search(r"每小时|每一小时", msg):
        return CronMatch(True, "0 * * * *", _strip_time(msg), "每小时")

    # 每 N 分钟
    m = re.search(r"每\s*(\d{1,3})\s*分钟", msg)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 59:
            return CronMatch(True, f"*/{n} * * * *", _strip_time(msg), f"每{n}分钟")

    # 每 N 小时
    m = re.search(r"每\s*(\d{1,3})\s*小时", msg)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 23:
            return CronMatch(True, f"0 */{n} * * *", _strip_time(msg), f"每{n}小时")

    return CronMatch()


def _strip_time(msg: str) -> str:
    """去掉时间关键词，保留任务主体"""
    cleaned = re.sub(_TIME_KEYWORDS, "", msg).strip()
    # 去掉首尾标点和多余空格
    cleaned = re.sub(r"^[，,。.\s]+|[，,。.\s]+$", "", cleaned).strip()
    return cleaned or msg
