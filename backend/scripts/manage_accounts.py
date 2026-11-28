from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.provisioning import (  # noqa: E402
    BOOTSTRAP_MESSAGES,
    AccountResult,
    ProvisioningError,
    bootstrap_state,
    enabled_admin_count,
    handover_admin,
    provision_teacher,
    recover_admin,
)

STATUS_COMMAND = "status"


def _result_line(result: AccountResult) -> str:
    line = (
        f"[accounts] {result.action} 成功：{result.role} 账号 "
        f"{result.account}（{result.name}）"
    )
    if result.generated:
        line += f"\n[accounts] 初始密码: {result.password}（首次登录必须修改）"
    return line


def _add_actor_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--actor",
        default=None,
        help="执行人；留空时读取 OPS_ACTOR，仍为空则记为“校方授权人”",
    )


def _build_parser() -> argparse.ArgumentParser:
    actor_parent = argparse.ArgumentParser(add_help=False)
    _add_actor_argument(actor_parent)

    parser = argparse.ArgumentParser(
        description="受控账号运维：教师开通、班委交接与管理员恢复",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(STATUS_COMMAND, help="查看自举状态与可用管理员数量")

    teacher = subparsers.add_parser(
        "provision-teacher",
        parents=[actor_parent],
        help="开通教师账号（不改变现有班委与教师权限）",
    )
    teacher.add_argument("--account", required=True)
    teacher.add_argument("--name", required=True)
    teacher.add_argument("--password", default=None)
    teacher.add_argument("--class-id", type=int, default=1)

    handover = subparsers.add_parser(
        "handover-admin",
        parents=[actor_parent],
        help="班委交接：停用原班委并开通新班委",
    )
    handover.add_argument("--from", dest="previous_account", required=True)
    handover.add_argument("--account", required=True)
    handover.add_argument("--name", required=True)
    handover.add_argument("--password", default=None)
    handover.add_argument("--class-id", type=int, default=1)

    recover = subparsers.add_parser(
        "recover-admin",
        parents=[actor_parent],
        help="管理员恢复：重置管理员密码并撤销旧会话",
    )
    recover.add_argument("--account", required=True)
    recover.add_argument("--name", default=None)
    recover.add_argument("--password", default=None)
    recover.add_argument("--class-id", type=int, default=1)

    return parser


def _print_status(db) -> None:
    state = bootstrap_state(db)
    print(f"[accounts] 状态: {state}")
    print(f"[accounts] 可用管理员: {enabled_admin_count(db)}")
    print(f"[accounts] 说明: {BOOTSTRAP_MESSAGES[state]}")
    if state == "needs_recovery":
        print(
            "[accounts] 恢复示例: python scripts/manage_accounts.py "
            "recover-admin --account admin"
        )
    elif state == "needs_initialization":
        print("[accounts] 初始化: 启动后端容器或执行 scripts/bootstrap_admin.py")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        with SessionLocal() as db:
            if args.command == STATUS_COMMAND:
                _print_status(db)
                return 0
            if args.command == "provision-teacher":
                result = provision_teacher(
                    db,
                    account=args.account,
                    name=args.name,
                    password=args.password,
                    actor=args.actor,
                    class_id=args.class_id,
                )
                print(_result_line(result))
                return 0
            if args.command == "handover-admin":
                handover = handover_admin(
                    db,
                    previous_account=args.previous_account,
                    account=args.account,
                    name=args.name,
                    password=args.password,
                    actor=args.actor,
                    class_id=args.class_id,
                )
                print(
                    f"[accounts] 已停用班委账号 {handover.previous_account}，"
                    f"原会话已失效"
                )
                print(_result_line(handover.new_admin))
                return 0
            result = recover_admin(
                db,
                account=args.account,
                name=args.name,
                password=args.password,
                actor=args.actor,
                class_id=args.class_id,
            )
            print(_result_line(result))
            return 0
    except ProvisioningError as exc:
        print(f"[accounts] 失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[accounts] 执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
