"""配置校验 - 启动时验证关键配置，快速失败

避免运行时才发现问题（如 JWT_SECRET 过短、LLM_API_KEY 为空）。
校验不通过抛出 ValueError，阻止应用启动。
"""
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("config_validator")

# 最小密钥长度
_MIN_SECRET_LEN = 32
_EXPECTED_ENCRYPT_KEY_LEN = 32


async def validate_config() -> list[str]:
    """校验配置，返回警告列表（不阻断启动）

    致命错误（如 LLM_API_KEY 为空）记录为 ERROR 但不阻断（允许无 LLM 模式启动）。
    安全配置问题记录为 WARNING。
    """
    warnings: list[str] = []

    # 1. LLM_API_KEY（不阻断，但警告）
    if not settings.llm_api_key or settings.llm_api_key.startswith("sk-local-dev"):
        warnings.append(
            "LLM_API_KEY 未配置或为占位符，Agent 对话功能将不可用。"
            "请在 .env 中填入真实 API Key。"
        )

    # 2. JWT_SECRET 安全性
    if len(settings.jwt_secret) < _MIN_SECRET_LEN:
        warnings.append(
            f"JWT_SECRET 长度 {len(settings.jwt_secret)} < {_MIN_SECRET_LEN}，"
            "存在暴力破解风险，建议使用 ≥32 字符的随机字符串。"
        )
    if settings.jwt_secret in ("change_me_please", "please_change_this"):
        warnings.append("JWT_SECRET 使用默认值，生产环境必须修改！")

    # 2.1 JWT_SECRET_PREVIOUS（轮换密钥校验）
    if settings.jwt_secret_previous:
        if len(settings.jwt_secret_previous) < _MIN_SECRET_LEN:
            warnings.append(
                f"JWT_SECRET_PREVIOUS 长度 {len(settings.jwt_secret_previous)} < {_MIN_SECRET_LEN}，"
                "轮换密钥强度不足。"
            )
        if settings.jwt_secret_previous == settings.jwt_secret:
            warnings.append("JWT_SECRET_PREVIOUS 与 JWT_SECRET 相同，轮换无意义。")

    # 3. ENCRYPT_KEY 长度
    if len(settings.encrypt_key) != _EXPECTED_ENCRYPT_KEY_LEN:
        warnings.append(
            f"ENCRYPT_KEY 长度 {len(settings.encrypt_key)} ≠ {_EXPECTED_ENCRYPT_KEY_LEN}，"
            "AES 加密可能失败，请使用 32 字符密钥。"
        )
    if settings.encrypt_key in ("change_me_32_chars_please!!!", "please_change_32_chars_key_random!!!"):
        warnings.append("ENCRYPT_KEY 使用默认值，生产环境必须修改！")

    # 4. 生产环境强制检查
    if settings.is_production:
        if settings.app_debug:
            warnings.append("生产环境 APP_DEBUG=true，存在安全风险，建议关闭。")
        if "*" in str(getattr(settings, "cors_origins", ["*"])):
            warnings.append("生产环境 CORS allow_origins=* 不安全，建议配置具体域名。")

    # 5. 部署规模一致性检查
    if settings.cache_backend == "redis" and settings.vector_backend == "local":
        warnings.append(
            "CACHE_BACKEND=redis 但 VECTOR_BACKEND=local，"
            "多实例部署时向量数据不共享，建议大企业配置 VECTOR_BACKEND=milvus。"
        )

    # 输出警告
    for w in warnings:
        log.warning("Config validation: {}", w)

    if not warnings:
        log.info("Config validation passed, no warnings")
    return warnings
