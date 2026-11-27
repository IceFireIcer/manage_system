from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.provisioning import (  # noqa: E402
    BOOTSTRAP_MESSAGES,
    ProvisioningError,
    bootstrap_initial_admin,
    bootstrap_state,
)

DEFAULT_ACCOUNT = "admin"
DEFAULT_NAME = "系统管理员"
RECOVERY_HINT = (
    "执行 python scripts/manage_accounts.py recover-admin --account <管理员账号>"
)


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def bootstrap_admin() -> int:
    account = _env("ADMIN_INIT_ACCOUNT", DEFAULT_ACCOUNT)
    name = _env("ADMIN_INIT_NAME", DEFAULT_NAME)
    password = os.getenv("ADMIN_INIT_PASSWORD") or None

    with SessionLocal() as db:
        state = bootstrap_state(db)
        if state == "ready":
            print("[bootstrap] 管理员账号已存在，跳过")
            return 0
        if state == "needs_recovery":
            print(f"[bootstrap] 需恢复：{BOOTSTRAP_MESSAGES['needs_recovery']}；{RECOVERY_HINT}")
            return 2
        result = bootstrap_initial_admin(
            db,
            account=account,
            name=name,
            password=password,
        )

    if result is None:
        print("[bootstrap] 管理员账号已存在，跳过")
    elif result.generated:
        print(
            f"[bootstrap] 已创建管理员账号: {result.account}；初始密码: {result.password}"
            "（请首次登录后立即修改并妥善保存）"
        )
    else:
        print(
            f"[bootstrap] 已按环境变量创建管理员账号: {result.account}"
            "（密码不显示，请使用 ADMIN_INIT_PASSWORD 的值登录，首次登录必须修改）"
        )
    return 0


def main() -> int:
    try:
        return bootstrap_admin()
    except ProvisioningError as exc:
        print(f"[bootstrap] 需初始化/需恢复：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"[bootstrap] 需初始化：管理员初始化失败（{exc}），"
            "请确认数据库已就绪后执行 python scripts/manage_accounts.py status",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
