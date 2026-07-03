"""数据库异步会话管理 - SQLAlchemy 2.0 async

支持 backend 切换：
- sqlite：单机/小企业部署（aiosqlite 驱动，无连接池开销）
- postgresql：多实例/大型企业（asyncpg 驱动，连接池）

通过 settings.db_backend 切换，统一通过 db_dsn 获取连接串。
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("db")


def _build_engine():
    """按 backend 创建异步引擎（SQLite 与 PostgreSQL 参数差异较大）"""
    if settings.db_backend == "sqlite":
        # SQLite 单文件：自动创建父目录，无连接池
        db_path = settings.sqlite_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=settings.app_debug,
            future=True,
            # SQLite 写并发需要短事务，check_same_thread 由 aiosqlite 处理
        )
        log.info("Using SQLite backend: {}", db_path)
        return engine

    # PostgreSQL：连接池 + 健康检查
    engine = create_async_engine(
        settings.postgres_dsn,
        echo=settings.app_debug,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    log.info("Using PostgreSQL backend: {}:{}", settings.postgres_host, settings.postgres_port)
    return engine


# 异步引擎（按配置选择 backend）
engine = _build_engine()

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话上下文管理器"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            log.error("DB session error: {}", e)
            raise
        finally:
            await session.close()


async def get_session() -> AsyncSession:
    """FastAPI依赖：注入数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_db_health() -> bool:
    """数据库健康检查"""
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.error("DB health check failed: {}", e)
        return False


async def close_db() -> None:
    """关闭数据库连接池"""
    await engine.dispose()
    log.info("DB engine disposed")
