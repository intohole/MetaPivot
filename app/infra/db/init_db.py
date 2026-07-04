"""数据库初始化 - 建表与默认数据"""
import asyncio

from sqlalchemy import text

from app.infra.db.session import async_session_factory, engine, Base
from app.infra.db.models_user_skill import UserORM, SkillORM, MCPServerORM
from app.infra.db.models_core import (
    WorkflowORM, WorkflowExecutionORM, AgentTaskORM, AgentTaskStepORM,
    AgentTaskEventORM,
    AuditLogORM, KnowledgeDocumentORM, IMChatORM, IMMessageORM, ConfigORM,
    ChatMessageORM, ChatSummaryORM, ScheduledTaskORM,
)
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.security import hash_password

log = get_logger("init_db")


async def init_db() -> None:
    """创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables created")

    await seed_admin_user()
    await seed_default_skills()
    await seed_configs()


async def seed_admin_user() -> None:
    """初始化默认管理员"""
    async with async_session_factory() as session:
        exists = await session.execute(
            text("SELECT 1 FROM users WHERE username = 'admin'")
        )
        if exists.scalar():
            return
        admin = UserORM(
            username="admin",
            password_hash=hash_password("admin123"),
            role="admin",
            im_accounts={},
            status="active",
        )
        session.add(admin)
        await session.commit()
        log.info("Default admin user created (admin/admin123) - 请立即修改密码")


async def seed_default_skills() -> None:
    """初始化默认Skill"""
    default_skills = [
        {
            "name": "knowledge_search",
            "description": "在企业知识库中检索文档。当用户询问公司制度、流程、FAQ等内部信息时使用。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "top_k": {"type": "integer", "description": "返回数量", "default": 5}
                },
                "required": ["query"]
            },
            "source_type": "function",
            "source_ref": "app.infra.rag.search",
            "permission": "user",
            "require_confirm": False,
            "tags": ["knowledge", "rag"]
        },
        {
            "name": "get_current_time",
            "description": "获取当前时间和日期。当用户询问时间、日期相关问题或需要时间上下文时调用。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "时区", "default": "Asia/Shanghai"}
                }
            },
            "source_type": "function",
            "source_ref": "app.infra.tools.time_tool.get_time",
            "permission": "user",
            "require_confirm": False,
            "tags": ["utility"]
        }
    ]
    async with async_session_factory() as session:
        for skill_data in default_skills:
            exists = await session.execute(
                text("SELECT 1 FROM skills WHERE name = :name"),
                {"name": skill_data["name"]},
            )
            if exists.scalar():
                continue
            session.add(SkillORM(**skill_data))
        await session.commit()
    log.info("Default skills seeded: {}", [s["name"] for s in default_skills])


async def seed_configs() -> None:
    """初始化系统配置"""
    default_configs = [
        ("agent.max_steps", "10", "agent", "Agent最大步数"),
        ("agent.max_retries", "3", "agent", "单步最大重试"),
        ("agent.timeout", "120", "agent", "Agent任务超时(秒)"),
        ("hitl.timeout", "300", "security", "HITL确认超时(秒)"),
        ("audit.retention_days", "180", "audit", "审计日志保留天数"),
    ]
    async with async_session_factory() as session:
        for key, value, category, desc in default_configs:
            exists = await session.execute(
                text("SELECT 1 FROM configs WHERE key = :key"),
                {"key": key},
            )
            if exists.scalar():
                continue
            session.add(ConfigORM(key=key, value=value, category=category, description=desc, updatable=True))
        await session.commit()
    log.info("Default configs seeded")


if __name__ == "__main__":
    asyncio.run(init_db())
