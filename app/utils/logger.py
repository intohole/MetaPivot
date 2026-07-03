"""MetaPivot 统一日志系统 - loguru，文件轮转保留3天"""
import sys
from pathlib import Path

from loguru import logger

from app.utils.config import settings


def setup_logger() -> None:
    """初始化日志系统：控制台+文件，保留3天"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.remove()  # 清除默认配置

    # 控制台输出（彩色）
    logger.add(
        sys.stdout,
        level=settings.app_log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        filter=lambda record: record["extra"].get("request_id") is None,
    )

    # 带request_id的控制台输出
    logger.add(
        sys.stdout,
        level=settings.app_log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<magenta>[{extra[request_id]}]</magenta> | "
            "<level>{message}</level>"
        ),
        filter=lambda record: record["extra"].get("request_id") is not None,
    )

    # 文件输出：按天轮转，保留3天（符合用户规则）
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level=settings.app_log_level,
        rotation="00:00",                              # 每天0点轮转
        retention=f"{settings.app_log_retention_days} days",  # 保留3天
        compression="zip",                            # 压缩旧日志
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} | {extra} | {message}"
        ),
        backtrace=True,
        diagnose=False,                                # 生产环境不暴露变量
    )

    # 错误日志单独文件
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        rotation="00:00",
        retention=f"{settings.app_log_retention_days} days",
        compression="zip",
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} | {extra} | {message}"
        ),
        backtrace=True,
        diagnose=True,
    )

    logger.info("Logger initialized | env={} level={}", settings.app_env, settings.app_log_level)


def get_logger(name: str = "metapivot"):
    """获取带模块名的logger"""
    return logger.bind(name=name)
