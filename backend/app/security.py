import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

ALGORITHM = "HS256"
TOKEN_COOKIE_NAME = "access_token"
UNTRUSTED_PROXY_DETAIL = "不可信代理来源"


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    settings = get_settings()
    expire_seconds = settings.jwt_expire_minutes * 60
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
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


def client_is_trusted_proxy(client_host: str, trusted_proxies: set[str]) -> bool:
    """判断来源 IP 是否属于可信代理，支持单 IP 与 CIDR 网段。"""
    if not trusted_proxies:
        return True
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host in trusted_proxies
    for entry in trusted_proxies:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            if entry == client_host:
                return True
    return False


def resolve_forwarded_scheme(
    direct_scheme: str,
    forwarded_proto: str | None,
    client_host: str,
    trusted_proxies: set[str],
) -> tuple[str, str | None]:
    """解析实际请求协议；仅当来源为可信代理时才采用 X-Forwarded-Proto。"""
    if not forwarded_proto:
        return (direct_scheme or "").lower(), None
    if not client_is_trusted_proxy(client_host, trusted_proxies):
        return (direct_scheme or "").lower(), UNTRUSTED_PROXY_DETAIL
    return forwarded_proto.split(",")[0].strip().lower(), None


def host_is_allowed(host_header: str, allowed_hosts: set[str]) -> bool:
    """校验 Host 来源；未配置允许清单时不限制。"""
    if not allowed_hosts:
        return True
    host = host_header.split(",")[0].strip().lower()
    if not host:
        return False
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    return host in allowed_hosts or hostname in allowed_hosts
