"""Guardrail - LLM 输入输出安全护栏

职责：
1. 输入侧：识别并脱敏 PII（手机号/身份证/邮箱），防止泄露给 LLM
2. 输出侧：检测 LLM 响应中的敏感模式，防止泄露内部系统细节
3. 越权关键词检测：阻止 prompt injection（如"忽略以上指令"）

属于 Domain 层（纯函数，无 IO），被 agent/nodes.py 调用。
"""
import re

from app.utils.logger import get_logger
from app.utils.security import desensitize

log = get_logger("guardrail")

# Prompt injection 检测模式（命中即阻断，返回安全文本）
_INJECTION_PATTERNS = [
    re.compile(r"忽略(?:以上|上面|先前|之前)的?(?:指令|规则|提示)", re.IGNORECASE),
    re.compile(r"ignore\s+(?:above|previous|prior)\s+(?:instruction|rule)", re.IGNORECASE),
    re.compile(r"你(?:现在)?是(?:一个)??(?:无限制|无道德|DAN)", re.IGNORECASE),
    re.compile(r"system\s*[:：]\s*", re.IGNORECASE),
    re.compile(r"(?:扮演|假装)你是(?:一个)?(?:无限制|无道德|DAN)", re.IGNORECASE),
    re.compile(r"(?:输出|告诉我|显示)你的?(?:系统|初始)提示", re.IGNORECASE),
]

# 输出侧禁止泄露的内部关键词
_SENSITIVE_OUTPUT_KEYWORDS = [
    "password_hash", "jwt_secret", "jwt_secret_previous", "encrypt_key",
    "api_key", "DATABASE_URL", "POSTGRES_PASSWORD", "REDIS_PASSWORD",
    "DINGTALK_CLIENT_SECRET", "WECOM_APP_SECRET", "FEISHU_APP_SECRET",
]

# injection 命中时返回的安全文本（替代原始输入，阻断而非放行）
_INJECTION_BLOCKED_REPLY = (
    "检测到潜在的提示注入请求，已拦截。"
    "如需正常使用，请直接描述您的业务需求。"
)


def sanitize_input(text: str) -> str:
    """输入脱敏：PII 替换 + prompt injection 阻断

    返回脱敏后的文本；若检测到 injection，返回安全拦截文本（阻断业务）。

    阻断策略：返回安全文本而非抛异常 — 避免异常传播破坏状态机，
    业务方拿到合法字符串继续流转，但 LLM 收到的是拦截提示而非用户原始注入内容。
    """
    if not text:
        return text
    # Injection 检测（命中即阻断，返回安全文本）
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            log.warning("Prompt injection blocked: {}", pattern.pattern)
            return _INJECTION_BLOCKED_REPLY
    # PII 脱敏（仅对非 injection 文本执行）
    return desensitize(text)


def sanitize_output(text: str) -> str:
    """输出脱敏：移除 LLM 响应中可能泄露的敏感关键词"""
    if not text:
        return text
    result = text
    for kw in _SENSITIVE_OUTPUT_KEYWORDS:
        if kw.lower() in result.lower():
            log.warning("Sensitive keyword in LLM output: {}", kw)
            result = re.sub(re.escape(kw), "***", result, flags=re.IGNORECASE)
    return result


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """批量脱敏对话历史（仅脱敏 user/tool 角色，不脱敏 system/assistant）"""
    sanitized = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("user", "tool") and isinstance(content, str):
            content = sanitize_input(content)
        sanitized.append({**msg, "content": content})
    return sanitized
