"""首次部署管理员自举初始化。

幂等：已存在启用的 admin 账号时直接跳过，不重复创建。
密码优先读取 ``ADMIN_INIT_PASSWORD``，未设置时随机生成并打印到 stdout，
生产环境可通过 ``docker compose logs backend`` 查看初始密码。

用法（容器启动时由 CMD 自动执行）：

    python scripts/bootstrap_admin.py
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from sqlalchemy import select  # noqa: E402

DEFAULT_ACCOUNT = "admin"
DEFAULT_NAME = "系统管理员"


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def bootstrap_admin() -> int:
    account = _env("ADMIN_INIT_ACCOUNT", DEFAULT_ACCOUNT)
    name = _env("ADMIN_INIT_NAME", DEFAULT_NAME)

    with SessionLocal() as db:
        admin_exists = db.scalar(
            select(User.id).where(User.role == "admin", User.status == "enabled").limit(1)
        )
        if admin_exists is not None:
            print("[bootstrap] 管理员账号已存在，跳过")
            return 0

        account_exists = db.scalar(select(User.id).where(User.account == account).limit(1))
        if account_exists is not None:
            print(f"[bootstrap] 账号 {account} 已被非管理员用户占用，跳过创建")
            return 0

        password = os.getenv("ADMIN_INIT_PASSWORD") or secrets.token_urlsafe(12)
        db.add(
            User(
                account=account,
                password_hash=hash_password(password),
                name=name,
                role="admin",
                status="enabled",
            )
        )
        db.commit()

    print(
        f"[bootstrap] 已创建管理员账号: {account}；初始密码: {password}"
        "（请首次登录后立即修改并妥善保存）"
    )
    return 0


def main() -> int:
    try:
        return bootstrap_admin()
    except Exception as exc:
        print(
            f"[bootstrap] 警告：管理员初始化失败（{exc}），"
            "请确认数据库已就绪后手动执行本脚本",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
