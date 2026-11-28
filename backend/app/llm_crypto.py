"""LLM API Key 加密存储。

使用独立的 SETTINGS_ENCRYPTION_KEY 派生 Fernet 密钥，与 JWT 密钥相互隔离。
密钥缺失时拒绝保存明文 Key；解密失败（如密钥轮换）时返回 None 并记录告警。
"""

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)

MASK_PREFIX = "••••"
MASK_FALLBACK = "••••••"
MISSING_KEY_DETAIL = "未配置 SETTINGS_ENCRYPTION_KEY，无法保存 API Key"


class EncryptionKeyMissing(RuntimeError):
    """SETTINGS_ENCRYPTION_KEY 未配置时抛出。"""


def _secret() -> str:
    return (get_settings().settings_encryption_key or "").strip()


def encryption_available() -> bool:
    return bool(_secret())


@lru_cache(maxsize=4)
def _fernet_for(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    secret = _secret()
    if not secret:
        raise EncryptionKeyMissing(MISSING_KEY_DETAIL)
    token = _fernet_for(secret).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(token: str | None) -> str | None:
    secret = _secret()
    if not secret or not token:
        return None
    try:
        return _fernet_for(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("LLM API Key 解密失败，可能 SETTINGS_ENCRYPTION_KEY 已轮换")
        return None


def mask_secret(plaintext: str | None, *, available: bool = True) -> str | None:
    if not available:
        return MASK_FALLBACK
    if not plaintext:
        return None
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"{MASK_PREFIX}{tail}"


def masked_api_key(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    plaintext = decrypt_secret(encrypted)
    return mask_secret(plaintext) if plaintext else MASK_FALLBACK
