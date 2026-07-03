"""ConfigService - 系统配置管理

职责：
1. 配置项查询（按分类筛选）
2. 配置更新（仅可更新项 updatable=true）
3. 内部读取配置值（运行时获取）
4. 启动时缓存到 Redis 提升读取性能
"""
from typing import Optional

from sqlalchemy import select

from app.infra.cache.redis_client import cache_delete, cache_get, cache_set
from app.infra.db.models_core import ConfigORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("config_service")

_CONFIG_CACHE_TTL = 3600  # 缓存1小时
_CACHE_PREFIX = "config:"


class ConfigService:
    """配置服务单例"""

    async def list_configs(self, category: str = "") -> list[dict]:
        """查询配置列表"""
        async with get_db_session() as session:
            stmt = select(ConfigORM)
            if category:
                stmt = stmt.where(ConfigORM.category == category)
            stmt = stmt.order_by(ConfigORM.category, ConfigORM.key)
            items = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(c) for c in items]

    async def get_config(self, key: str, use_cache: bool = True) -> Optional[str]:
        """读取配置值（带缓存）"""
        if use_cache:
            cached = await cache_get(f"{_CACHE_PREFIX}{key}")
            if cached is not None:
                return cached
        async with get_db_session() as session:
            config = await session.get(ConfigORM, key)
            if config is None:
                return None
            if use_cache:
                await cache_set(f"{_CACHE_PREFIX}{key}", config.value, _CONFIG_CACHE_TTL)
            return config.value

    async def get_config_int(self, key: str, default: int = 0) -> int:
        """读取整型配置"""
        value = await self.get_config(key)
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    async def update_config(self, key: str, value: str) -> dict:
        """更新配置（仅 updatable=true 的项可更新）"""
        async with get_db_session() as session:
            config = await session.get(ConfigORM, key)
            if config is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"配置 {key} 不存在", 404)
            if not config.updatable:
                raise AppError(ErrorCode.VALIDATION_ERROR, f"配置 {key} 不可更新", 400)
            config.value = value
            await session.flush()
            # 失效缓存
            await cache_delete(f"{_CACHE_PREFIX}{key}")
            log.info("Config updated: {} = {}", key, value[:50] if len(value) > 50 else value)
            return {
                "key": config.key, "value": config.value,
                "updated_at": config.updated_at.isoformat() if config.updated_at else None,
            }

    async def get_config_dict(self, prefix: str) -> dict[str, str]:
        """批量读取某前缀的配置项"""
        async with get_db_session() as session:
            stmt = select(ConfigORM).where(ConfigORM.key.like(f"{prefix}%"))
            items = (await session.execute(stmt)).scalars().all()
            return {c.key: c.value for c in items}

    def _to_dict(self, c: ConfigORM) -> dict:
        return {
            "key": c.key,
            "value": c.value,
            "category": c.category,
            "description": c.description,
            "updatable": c.updatable,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }


config_service = ConfigService()
