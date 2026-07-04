"""MetaPivot 统一日志系统 - loguru，文件轮转保留3天

通过 contextvars patcher 实现 request_id/trace_id 跨 asyncio.create_task 传播：
- 主请求中间件 set_request_context() 写入 contextvars
- loguru patcher 在每条日志写入前从 contextvars 注入 record["extra"]
- 后台任务通过 contextvars.copy_context() 透传，日志自动带上 request_id

生产环境（app_log_format=json）输出结构化 JSON，便于 ELK/Loki 采集；
开发环境输出彩色文本，提升可读性。
"""
import json
import sys
from pathlib import Path

from loguru import logger

from app.utils.config import settings


def _context_patcher(record: dict) -> None:
    """loguru patcher：从 contextvars 注入 request_id/trace_id 到 record extra"""
    from app.utils.context import get_request_id, get_trace_id, get_user_id
    if "request_id" not in record["extra"]:
        rid = get_request_id()
        if rid:
            record["extra"]["request_id"] = rid
    if "trace_id" not in record["extra"]:
        tid = get_trace_id()
        if tid:
            record["extra"]["trace_id"] = tid
    if "user_id" not in record["extra"]:
        uid = get_user_id()
        if uid:
            record["extra"]["user_id"] = uid


def _json_serializer(message) -> str:
    """JSON sink：将 loguru record 序列化为单行 JSON（ELK/Loki 友好）

    loguru format callable 的返回值会经过 format_map 处理，
    因此 JSON 中的 { } 必须转义为 {{ }} 以避免 KeyError。
    某些边界场景（如 shutdown 阶段）可能直接传入 record dict，
    此处做兼容处理避免 AttributeError 导致所有日志静默丢失。
    """
    record = message.record if hasattr(message, "record") else message
    if not isinstance(record, dict):
        return str(message)
    payload = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    # 合并 extra（request_id/trace_id/user_id 等上下文）
    extra = {k: v for k, v in record["extra"].items() if k != "name"}
    if extra:
        payload["extra"] = extra
    if record["exception"]:
        import traceback as _tb
        exc = record["exception"]
        payload["exception"] = "".join(_tb.format_exception(exc.type, exc.value, exc.traceback))
    json_str = json.dumps(payload, ensure_ascii=False, default=str)
    # 替换 < > 为 \u003c \u003e 避免 Colorizer 将 <tag> 误解析为颜色指令
    # \u003c/\u003e 是合法 JSON 转义，不包含字面 < >，不影响 JSON 解析
    json_str = json_str.replace("<", "\\u003c").replace(">", "\\u003e")
    # 转义 { } → {{ }} 避免 format_map 将 JSON 大括号误解析为占位符
    # 末尾追加 \n 确保每条日志独占一行（callable format 不自动加换行）
    return json_str.replace("{", "{{").replace("}", "}}") + "\n"


def setup_logger() -> None:
    """初始化日志系统：控制台+文件，保留3天"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.remove()
    logger.configure(patcher=_context_patcher)

    use_json = settings.app_log_format == "json"

    if use_json:
        # JSON 格式（生产环境）— 控制台 + 文件统一 JSON，便于日志采集
        logger.add(sys.stdout, level=settings.app_log_level, format=_json_serializer)
        logger.add(
            log_dir / "app_{time:YYYY-MM-DD}.log",
            level=settings.app_log_level, format=_json_serializer,
            rotation="00:00", retention=f"{settings.app_log_retention_days} days",
            compression="zip", encoding="utf-8", backtrace=False, diagnose=False,
        )
        logger.add(
            log_dir / "error_{time:YYYY-MM-DD}.log",
            level="ERROR", format=_json_serializer,
            rotation="00:00", retention=f"{settings.app_log_retention_days} days",
            compression="zip", encoding="utf-8", backtrace=False, diagnose=False,
        )
    else:
        # 彩色文本格式（开发环境）— 按 request_id 分流提升可读性
        _setup_text_sinks(log_dir)

    logger.info(
        "Logger initialized | env={} level={} format={}",
        settings.app_env, settings.app_log_level, "json" if use_json else "text",
    )


def _setup_text_sinks(log_dir: Path) -> None:
    """开发环境彩色文本 sink 配置"""
    text_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    req_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<magenta>[{extra[request_id]}]</magenta> | "
        "<level>{message}</level>"
    )
    file_fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {extra} | {message}"
    )
    # 无 request_id 的控制台输出（启动阶段）
    logger.add(
        sys.stdout, level=settings.app_log_level, format=text_fmt,
        filter=lambda r: not r["extra"].get("request_id"),
    )
    # 带 request_id 的控制台输出（请求处理 + 后台任务）
    logger.add(
        sys.stdout, level=settings.app_log_level, format=req_fmt,
        filter=lambda r: bool(r["extra"].get("request_id")),
    )
    # 文件输出：按天轮转，保留3天
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level=settings.app_log_level, format=file_fmt,
        rotation="00:00", retention=f"{settings.app_log_retention_days} days",
        compression="zip", encoding="utf-8", backtrace=True, diagnose=False,
    )
    # 错误日志单独文件
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        level="ERROR", format=file_fmt,
        rotation="00:00", retention=f"{settings.app_log_retention_days} days",
        compression="zip", encoding="utf-8", backtrace=True, diagnose=False,
    )


def get_logger(name: str = "metapivot"):
    """获取带模块名的logger"""
    return logger.bind(name=name)
