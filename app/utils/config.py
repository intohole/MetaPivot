"""MetaPivot 企业IM自动化办公服务 - 应用配置"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，从环境变量/.env加载，禁止硬编码"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_name: str = "MetaPivot"
    app_version: str = "1.0.0"
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_log_retention_days: int = 3
    app_log_format: Literal["text", "json"] = "text"  # 生产环境推荐 json（ELK/Loki 采集）

    # 部署规模（资源可伸缩）— 小企业可用 sqlite/memory/local，零外部依赖
    db_backend: Literal["sqlite", "postgresql"] = "postgresql"
    cache_backend: Literal["memory", "redis"] = "redis"
    vector_backend: Literal["local", "milvus", "chroma"] = "local"
    memory_backend: Literal["memory", "db", "semantic"] = "db"
    scheduler_backend: Literal["async", "celery"] = "async"
    sqlite_path: str = "data/metapivot.db"

    # LLM
    llm_provider: Literal["kimi", "qwen", "glm", "deepseek"] = "kimi"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.moonshot.cn/v1"
    llm_model: str = "kimi-k2-6"
    llm_timeout: int = 60
    llm_max_steps: int = 10
    llm_temperature: float = 0.3
    # embedding 模型（semantic memory / RAG 用，各 provider 模型名不同）
    # - OpenAI: text-embedding-3-small / text-embedding-ada-002
    # - Kimi: text-embedding-v1（部分账号支持）
    # - Qwen: text-embedding-v2 / text-embedding-v3
    # 留空时用 ILLMProvider.embed 默认值（text-embedding-v3）
    llm_embed_model: str = ""

    # PostgreSQL（db_backend=postgresql 时使用）
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "metapivot"
    postgres_user: str = "metapivot"
    postgres_password: str = ""

    # Redis（cache_backend=redis 时使用）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # Milvus（vector_backend=milvus 时使用）
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "knowledge_chunks"

    # Chroma（vector_backend=chroma 或 memory_backend=semantic 时使用）
    # - 留空 chroma_host 时用 PersistentClient 本地持久化（单机/小企业，零外部服务依赖）
    # - 配置 chroma_host 时用 HttpClient 远程服务（多实例共享）
    chroma_host: str = ""
    chroma_port: int = 8001
    chroma_path: str = "data/chroma"
    # 语义记忆 collection 名（memory_backend=semantic 时存消息 embedding）
    chroma_memory_collection: str = "agent_memory"
    # 语义记忆触发事实抽取的消息间隔（Mem0 风格 consolidate，每 N 条触发一次）
    memory_consolidate_interval: int = 10

    # 钉钉
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_enabled: bool = False

    # 企业微信
    wecom_corp_id: str = ""
    wecom_app_secret: str = ""
    wecom_agent_id: str = ""
    wecom_token: str = ""
    wecom_encoding_aes_key: str = ""
    wecom_enabled: bool = False

    # 飞书
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_enabled: bool = False

    # 安全
    jwt_secret: str = Field(default="", min_length=32)
    jwt_secret_previous: str = ""  # 轮换时填旧密钥，留空表示无轮换
    jwt_kid_primary: str = "primary"  # 当前主密钥标识
    jwt_expires_in: int = 3600
    jwt_algorithm: str = "HS256"
    encrypt_key: str = Field(default="", min_length=32)

    # 限流
    rate_limit_im_qps: int = 20
    rate_limit_api_qps: int = 60

    # HITL
    hitl_timeout_seconds: int = 300

    # Agent 核心深化（Phase 1）
    agent_task_timeout: int = 300  # 任务级超时（秒），asyncio.wait_for 包裹 runner
    agent_judge_k: int = 4  # L4 Judge 评估间隔（每 K 步评估一次）

    # OTel + Langfuse 可观测性（Phase 4）
    otel_enabled: bool = False  # OTel SDK 总开关（False 时使用 NoopTracer，零依赖）
    otel_endpoint: str = ""  # OTLP exporter endpoint（可选，独立 OTel collector）
    langfuse_enabled: bool = False  # Langfuse 集成开关
    langfuse_host: str = ""  # Langfuse host（如 https://cloud.langfuse.com）
    langfuse_public_key: str = ""  # Langfuse public key
    langfuse_secret_key: str = ""  # Langfuse secret key

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def db_dsn(self) -> str:
        """统一数据库 DSN（按 db_backend 切换 SQLite/PostgreSQL）"""
        if self.db_backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        return self.postgres_dsn

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """单例配置，避免重复读取.env"""
    return Settings()


settings = get_settings()
