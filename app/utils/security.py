"""安全工具：JWT、密码哈希、加解密"""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt

from app.utils.config import settings

# 脱敏正则（注意顺序：长模式优先，避免短模式先行匹配导致部分脱敏）
_DESENSITIZE_PATTERNS = [
    (re.compile(r"\b\d{17}[\dXx]\b"), "id_card"),                          # 18位身份证（末位X/x）
    (re.compile(r"\b1[3-9]\d{9}\b"), "phone"),                              # 手机号
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "email"),  # 邮箱
]


def hash_password(password: str) -> str:
    """密码哈希（bcrypt，兼容 passlib hash 格式 $2b$）"""
    # bcrypt 限制 72 字节，超长截断（与 passlib 行为一致）
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """密码校验（兼容 passlib 生成的 $2b$ hash）"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(payload: dict, expires_in: Optional[int] = None) -> str:
    """生成JWT"""
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=expires_in or settings.jwt_expires_in
    )
    to_encode = {**payload, "exp": expire, "iat": datetime.now(timezone.utc)}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """解析JWT，失败抛异常"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def compute_signature(timestamp: str, secret: str, *parts: str) -> str:
    """计算HMAC-SHA256签名（用于IM回调校验）"""
    string_to_sign = "\n".join([timestamp, secret, *parts])
    return hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sha256_hash(text: str) -> str:
    """SHA256哈希（用于审计日志输入脱敏）"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def desensitize(text: str) -> str:
    """数据脱敏：手机号/身份证/邮箱等"""
    if not text:
        return text
    result = text
    for pattern, kind in _DESENSITIZE_PATTERNS:
        if kind == "phone":
            result = pattern.sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], result)
        elif kind == "id_card":
            result = pattern.sub(lambda m: m.group()[:6] + "********" + m.group()[-4:], result)
        elif kind == "email":
            result = pattern.sub(
                lambda m: m.group()[0] + "***@" + m.group().split("@")[1], result
            )
    return result


def encrypt_aes(data: str, key: Optional[str] = None) -> bytes:
    """AES加密（企微回调用，简化实现）"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.backends import default_backend

    key_bytes = (key or settings.encrypt_key).encode("utf-8")[:32]
    key_bytes = key_bytes.ljust(32, b"\0")
    iv = b"\0" * 16  # 生产环境应使用随机IV
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()
