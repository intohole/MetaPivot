"""数据库初始化 - 建表与默认数据"""
import asyncio

from sqlalchemy import text

from app.infra.db.session import async_session_factory, engine, Base
from app.infra.db.models_user_skill import (
    UserORM, SkillORM, MCPServerORM,
    SkillExecutionORM, SkillRevisionORM, SkillDraftORM,  # noqa: F401  Skill自进化：确保.metadata 注册建表
)
from app.infra.db.models_core import (
    WorkflowORM, WorkflowExecutionORM, WorkflowTemplateORM,
    AuditLogORM, KnowledgeDocumentORM, IMChatORM, IMMessageORM, ConfigORM,
    ScheduledTaskORM,
)
from app.infra.db.models_agent import (  # noqa: F401  Sprint 8.1: Agent/Chat 模型独立文件，确保.metadata 注册建表
    AgentTaskORM, AgentTaskStepORM, AgentTaskEventORM,
    ChatMessageORM, ChatSummaryORM,
)
from app.infra.db.models_webhook import WebhookORM  # noqa: F401  Phase 2: 确保.metadata 注册
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
    await seed_workflow_templates()


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


async def seed_workflow_templates() -> None:
    """初始化 SOP 工作流模板库

    沉淀高频办公自动化场景，用户一键实例化为工作流。
    模板覆盖：日常通知/知识检索/周报生成 三类典型 SOP。
    """
    # 复用 knowledge_search skill 的 id 引用：实例化后用户可在编辑器替换为实际 skill_id
    templates = [
        {
            "name": "每日站会提醒",
            "description": "工作日每天 10:00 向指定群发送站会提醒消息，含议程模板。",
            "category": "daily",
            "definition": {
                "nodes": [
                    {"id": "n_start", "type": "start", "config": {}},
                    {"id": "n_msg", "type": "send_message", "config": {
                        "channel": "api",
                        "text": "📅 每日站会提醒\n\n各位同学，今天 10:30 开始站会，请准备：\n1. 昨日完成事项\n2. 今日计划事项\n3. 阻塞/风险\n\n议题：${agenda}",
                    }},
                    {"id": "n_end", "type": "end", "config": {}},
                ],
                "edges": [
                    {"source": "n_start", "target": "n_msg"},
                    {"source": "n_msg", "target": "n_end"},
                ],
                "variables": [],
            },
            "trigger_template": {"type": "schedule", "cron_expr": "0 10 * * 1-5"},
            "input_schema": {
                "type": "object",
                "properties": {
                    "agenda": {"type": "string", "description": "今日议程", "default": "常规同步"},
                    "chat_id": {"type": "string", "description": "目标群聊ID"},
                },
                "required": ["chat_id"],
            },
            "tags": ["站会", "提醒", "日常"],
        },
        {
            "name": "知识库查询",
            "description": "在企业知识库检索关键词，返回匹配文档片段。手动触发，适合按需查询。",
            "category": "general",
            "definition": {
                "nodes": [
                    {"id": "n_start", "type": "start", "config": {}},
                    {"id": "n_search", "type": "skill_call", "config": {
                        "skill_id": "knowledge_search",
                        "args": {"query": "${query}", "top_k": 5},
                    }},
                    {"id": "n_end", "type": "end", "config": {}},
                ],
                "edges": [
                    {"source": "n_start", "target": "n_search"},
                    {"source": "n_search", "target": "n_end"},
                ],
                "variables": [],
            },
            "trigger_template": {"type": "manual"},
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                },
                "required": ["query"],
            },
            "tags": ["知识库", "检索", "RAG"],
        },
        {
            "name": "周报生成",
            "description": "每周五 18:00 基于本周工作摘要生成周报并发送到指定群。",
            "category": "weekly",
            "definition": {
                "nodes": [
                    {"id": "n_start", "type": "start", "config": {}},
                    {"id": "n_llm", "type": "llm_call", "config": {
                        "system": "你是企业办公助手，擅长撰写结构清晰的周报。",
                        "prompt": "请根据以下本周工作摘要，生成一份周报，包含【本周成果】【下周计划】【风险与阻塞】三部分：\n\n${week_summary}",
                    }},
                    {"id": "n_msg", "type": "send_message", "config": {
                        "channel": "api",
                        "text": "📋 本周周报\n\n${llm_content}",
                    }},
                    {"id": "n_end", "type": "end", "config": {}},
                ],
                "edges": [
                    {"source": "n_start", "target": "n_llm"},
                    {"source": "n_llm", "target": "n_msg"},
                    {"source": "n_msg", "target": "n_end"},
                ],
                "variables": [],
            },
            "trigger_template": {"type": "schedule", "cron_expr": "0 18 * * 5"},
            "input_schema": {
                "type": "object",
                "properties": {
                    "week_summary": {"type": "string", "description": "本周工作摘要文本"},
                    "chat_id": {"type": "string", "description": "目标群聊ID"},
                },
                "required": ["week_summary", "chat_id"],
            },
            "tags": ["周报", "汇报", "自动化"],
        },
    ]
    async with async_session_factory() as session:
        for tpl_data in templates:
            exists = await session.execute(
                text("SELECT 1 FROM workflow_templates WHERE name = :name"),
                {"name": tpl_data["name"]},
            )
            if exists.scalar():
                continue
            session.add(WorkflowTemplateORM(
                name=tpl_data["name"],
                description=tpl_data["description"],
                category=tpl_data["category"],
                definition=tpl_data["definition"],
                trigger_template=tpl_data["trigger_template"],
                input_schema=tpl_data["input_schema"],
                tags=tpl_data["tags"],
                visibility="public",
            ))
        await session.commit()
    log.info("Workflow templates seeded: {}", [t["name"] for t in templates])


if __name__ == "__main__":
    asyncio.run(init_db())
