from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

ALGORITHM = "HS256"
TOKEN_COOKIE_NAME = "access_token"


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, role: str, session_version: int = 0) -> tuple[str, int]:
    settings = get_settings()
    expire_seconds = settings.jwt_expire_minutes * 60
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "sv": session_version,
        "exp": datetime.now(UTC) + timedelta(seconds=expire_seconds),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)
    return token, expire_seconds


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, get_settings().jwt_secret_key, algorithms=[ALGORITHM]
        )
    except jwt.PyJWTError:
        return None
    return payload
