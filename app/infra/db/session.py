"""数据库异步会话管理 - SQLAlchemy 2.0 async"""
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

# 异步引擎
engine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.app_debug,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
)

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
