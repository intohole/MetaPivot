"""内置工具 - 时间查询"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


async def get_time(args: dict) -> dict:
    """获取当前时间

    args:
        timezone: 时区名，默认 Asia/Shanghai
    """
    tz_name = args.get("timezone", "Asia/Shanghai")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "timezone": str(tz),
        "unix_timestamp": int(now.timestamp()),
    }
