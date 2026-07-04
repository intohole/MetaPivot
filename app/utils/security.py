"""安全工具：JWT、密码哈希、加解密"""
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt

from app.utils.config import settings

# 银行卡号：13-19 位数字（Luhn 校验通过才脱敏，避免误伤长数字）
_BANK_CARD_PATTERN = re.compile(r"\b\d{13,19}\b")


def _is_valid_luhn(number: str) -> bool:
    """Luhn 算法校验银行卡号有效性"""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    # 从右起，偶数位 ×2，超 9 减 9
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _mask_bank_card(match: re.Match) -> str:
    """银行卡脱敏：保留前 4 后 4，中间 ****"""
    num = match.group()
    if not _is_valid_luhn(num):
        return num  # 非银行卡，原样返回
    return num[:4] + " **** **** " + num[-4:]


# 脱敏正则（注意顺序：长模式优先，避免短模式先行匹配导致部分脱敏）
_DESENSITIZE_PATTERNS = [
    (re.compile(r"\b\d{17}[\dXx]\b"), "id_card"),                          # 18位身份证（末位X/x）
    (re.compile(r"\b1[3-9]\d{9}\b"), "phone"),                              # 手机号
    (_BANK_CARD_PATTERN, "bank_card"),                                       # 银行卡（Luhn 校验）
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
    """生成 JWT（注入 kid header 标识当前主密钥）

    轮换流程：
    1. 生成新密钥 → 配置 JWT_SECRET=新密钥、JWT_SECRET_PREVIOUS=旧密钥
    2. 发布后，旧 token 仍能被 decode（grace period）
    3. 所有旧 token 过期后，清空 JWT_SECRET_PREVIOUS
    """
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=expires_in or settings.jwt_expires_in
    )
    to_encode = {**payload, "exp": expire, "iat": datetime.now(timezone.utc)}
    # 注入 kid header，decode 时据此选密钥
    headers = {"kid": settings.jwt_kid_primary}
    return jwt.encode(
        to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm, headers=headers
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """解析 JWT，支持 kid 多密钥并行校验（grace period）

    - 有 kid：按 kid 选密钥（primary → jwt_secret, previous → jwt_secret_previous）
    - 无 kid：向后兼容，走主密钥（支持轮换前签发的 token）
    - 主密钥失败且配置了 previous：尝试 previous（覆盖 kid 缺失但 token 是旧密钥签的场景）
    """
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
    except jwt.PyJWTError:
        kid = None  # header 解析失败，尝试主密钥

    if kid == "previous" and settings.jwt_secret_previous:
        secret = settings.jwt_secret_previous
    else:
        # 无 kid 或 kid=primary，走主密钥（向后兼容）
        secret = settings.jwt_secret

    try:
        return jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidSignatureError:
        # 主密钥失败时，若有 previous，尝试 previous
        if settings.jwt_secret_previous and secret != settings.jwt_secret_previous:
            return jwt.decode(
                token, settings.jwt_secret_previous, algorithms=[settings.jwt_algorithm]
            )
        raise


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
        elif kind == "bank_card":
            result = pattern.sub(_mask_bank_card, result)
    return result


def encrypt_aes(data: str, key: Optional[str] = None) -> bytes:
    """AES-CBC-256 加密（随机 IV 前置于密文，SHA-256 派生密钥）

    输出格式：iv(16B) || ciphertext
    解密时取前 16B 作为 IV，其余作为密文。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.backends import default_backend

    # SHA-256 派生密钥（替代 ljust(32, b"\0") 弱填充）
    key_bytes = hashlib.sha256((key or settings.encrypt_key).encode("utf-8")).digest()
    # 随机 IV（每次加密不同，防止差分攻击）
    iv = os.urandom(16)

    padder = padding.PKCS7(128).padder()
    padded = padder.update(data.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext  # IV 前置，解密方自行切分


def decrypt_aes(encrypted: bytes, key: Optional[str] = None) -> str:
    """AES-CBC-256 解密（密文前 16B 为 IV）

    与 encrypt_aes 配对使用。用于加密数据的回读场景。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.backends import default_backend

    if len(encrypted) < 16:
        raise ValueError("Encrypted data too short, IV missing")

    key_bytes = hashlib.sha256((key or settings.encrypt_key).encode("utf-8")).digest()
    iv, ciphertext = encrypted[:16], encrypted[16:]

    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
