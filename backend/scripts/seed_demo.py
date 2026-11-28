from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import business_time, inventory_ledger, stock_alerts  # noqa: E402
from app.agent import rules, service  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.consumption import window_flow  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Consumable, User  # noqa: E402
from app.roles import ensure_grants  # noqa: E402
from app.routers.requisitions import _create_requisition_impl  # noqa: E402
from app.schemas_requisitions import RequisitionCreate, RequisitionItemIn  # noqa: E402
from app.security import hash_password

ALLOWED_ENVS = {"demo", "dev"}
DEMO_PASSWORD = "Demo@12345"
DEMO_CLASS_ID = 1
OPENING_DAYS_AGO = 30
DEMO_ACCOUNTS = (
    ("teacher01", "王老师", "teacher"),
    ("admin01", "李班委", "admin"),
    ("student01", "张同学", "student"),
)


@dataclass(frozen=True)
class DemoConsumable:
    code: str
    name: str
    spec: str
    unit: str
    location: str
    safety_stock: int
    opening_stock: int
    issues: tuple[tuple[int, int], ...]
    status: str = "enabled"


DEMO_CONSUMABLES = (
    DemoConsumable(
        code="DEMO-GLOVE",
        name="一次性丁腈手套",
        spec="M 码",
        unit="盒",
        location="耗材柜 A-1",
        safety_stock=20,
        opening_stock=30,
        issues=((9, 6), (7, 6), (5, 6), (3, 6), (1, 6)),
    ),
    DemoConsumable(
        code="DEMO-WIRE",
        name="焊锡丝",
        spec="φ1.0mm",
        unit="卷",
        location="耗材柜 A-2",
        safety_stock=10,
        opening_stock=17,
        issues=((6, 4), (3, 4), (1, 4)),
    ),
    DemoConsumable(
        code="DEMO-PLA",
        name="3D 打印耗材",
        spec="PLA 1.75mm",
        unit="卷",
        location="耗材柜 B-1",
        safety_stock=20,
        opening_stock=46,
        issues=((13, 1), (12, 1), (11, 1), (10, 1), (6, 2), (5, 2), (4, 2), (3, 2), (2, 2), (1, 2)),
    ),
    DemoConsumable(
        code="DEMO-TAPE",
        name="绝缘胶带",
        spec="18mm×10m",
        unit="卷",
        location="耗材柜 B-2",
        safety_stock=5,
        opening_stock=40,
        issues=(),
    ),
    DemoConsumable(
        code="DEMO-OLD",
        name="停用演示耗材",
        spec="旧型号",
        unit="件",
        location="耗材柜 C-1",
        safety_stock=5,
        opening_stock=12,
        issues=(),
        status="disabled",
    ),
)

EXPECTED_TIERS = {
    "DEMO-GLOVE": rules.TIER_URGENT,
    "DEMO-WIRE": rules.TIER_MODERATE,
    "DEMO-PLA": rules.TIER_OBSERVE,
}


def _fail(message: str) -> int:
    print(f"[demo] 错误：{message}", file=sys.stderr)
    return 2


def _database_identifier(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.startswith("sqlite"):
        return Path(parts.path).name.lower()
    return parts.path.lstrip("/").split("/")[0].lower()


def _ensure_account(db, account: str, name: str, role: str) -> User:
    user = db.scalars(select(User).where(User.account == account)).first()
    if user is not None:
        return user
    user = User(
        account=account,
        password_hash=hash_password(DEMO_PASSWORD),
        name=name,
        role=role,
        class_id=DEMO_CLASS_ID,
        status="enabled",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    ensure_grants(db, user)
    return user


def _ensure_consumable(db, admin: User, spec: DemoConsumable) -> Consumable:
    consumable = db.scalars(
        select(Consumable).where(
            Consumable.class_id == DEMO_CLASS_ID, Consumable.code == spec.code
        )
    ).first()
    if consumable is not None:
        return consumable
    opened_at = business_time.day_start(
        business_time.today() - timedelta(days=OPENING_DAYS_AGO)
    )
    consumable = Consumable(
        code=spec.code,
        name=spec.name,
        spec=spec.spec,
        unit=spec.unit,
        location=spec.location,
        safety_stock=spec.safety_stock,
        current_stock=spec.opening_stock,
        class_id=DEMO_CLASS_ID,
        status=spec.status,
        created_at=opened_at,
        updated_at=opened_at,
    )
    db.add(consumable)
    db.flush()
    inventory_ledger.record_opening(db, consumable, admin)
    stock_alerts.sync_consumable(db, consumable, source="consumable")
    return consumable


def _ensure_issue(
    db, admin: User, student: User, consumable: Consumable, days_ago: int, quantity: int, key: str
) -> None:
    business_at: datetime = business_time.day_start(
        business_time.today() - timedelta(days=days_ago)
    ).replace(hour=10)
    payload = RequisitionCreate(
        items=[RequisitionItemIn(consumable_id=consumable.id, quantity=quantity)],
        user_id=student.id,
        created_at=business_at,
        remark="演示数据：三角色验收样例",
        backfill_reason="演示数据补录",
        request_id=f"{consumable.code.lower()}-{key}",
    )
    result = _create_requisition_impl(
        db, payload, admin, max_backfill_days=None, controlled=True
    )
    if isinstance(result, JSONResponse):
        if result.status_code >= 400:
            raise RuntimeError(f"演示领用被拒绝：{payload.request_id}")
        return
    if not result.order_no:
        raise RuntimeError(f"演示领用未生成回执：{payload.request_id}")


def _tier_line(db, consumable: Consumable) -> str:
    q7_start, prev_start, now_local = service.window_bounds()
    q7 = window_flow(db, consumable.id, q7_start, now_local).attributed_net
    prev7 = window_flow(db, consumable.id, prev_start, q7_start, end_inclusive=False).attributed_net
    tier, _reasons = rules.classify_tier(
        consumable.current_stock, consumable.safety_stock, q7, prev7
    )
    return (
        f"库存 {consumable.current_stock}/{consumable.safety_stock}，"
        f"近 7 天 {q7}、前 7 天 {prev7}，档位 {tier or '正常'}"
    )


def _verify(db) -> list[str]:
    problems: list[str] = []
    for spec in DEMO_CONSUMABLES:
        consumable = db.scalars(
            select(Consumable).where(
                Consumable.class_id == DEMO_CLASS_ID, Consumable.code == spec.code
            )
        ).first()
        if consumable is None:
            problems.append(f"缺少演示耗材 {spec.code}")
            continue
        events = inventory_ledger.list_events(db, consumable.id)
        expected = spec.opening_stock
        for event in events:
            if event.stock_after != event.stock_before + event.delta:
                problems.append(f"{spec.code} 第 {event.exec_seq} 步账本不平")
            expected = event.stock_after
        if expected != consumable.current_stock:
            problems.append(f"{spec.code} 账本余额与当前库存不一致")
        expected_tier = EXPECTED_TIERS.get(spec.code)
        if expected_tier is not None:
            tier_line = _tier_line(db, consumable)
            if f"档位 {expected_tier}" not in tier_line:
                problems.append(f"{spec.code} 未达到预期档位：{tier_line}")
    return problems


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="向隔离演示库装载三角色验收数据（拒绝正式环境）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认目标库是隔离演示库；不使用该参数时只打印计划",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    app_env = settings.app_env.strip().lower()
    if app_env not in ALLOWED_ENVS:
        return _fail(
            f"当前 APP_ENV={settings.app_env!r}，仅允许 demo/dev；"
            "正式环境禁止装载演示数据"
        )
    if app_env == "demo" and "demo" not in _database_identifier(
        settings.database_url
    ):
        return _fail(
            "APP_ENV=demo 的数据库标识不含 demo；请使用隔离演示库，"
            "避免写入正式数据库"
        )
    if not args.yes:
        print("[demo] 计划：创建演示账号与耗材，并补录历史领用；使用 --yes 执行")
        return 0

    try:
        with SessionLocal() as db:
            db.execute(select(User.id).limit(1))
    except Exception:
        return _fail("数据库不可用或未迁移，请先执行 alembic upgrade head")

    with SessionLocal() as db:
        admin = _ensure_account(db, "admin01", "李班委", "admin")
        teacher = _ensure_account(db, "teacher01", "王老师", "teacher")
        student = _ensure_account(db, "student01", "张同学", "student")
        _ = teacher
        for spec in DEMO_CONSUMABLES:
            consumable = _ensure_consumable(db, admin, spec)
            for index, (days_ago, quantity) in enumerate(spec.issues, start=1):
                _ensure_issue(db, admin, student, consumable, days_ago, quantity, str(index))
        db.commit()
        problems = _verify(db)
        if problems:
            for problem in problems:
                print(f"[demo] 校验失败：{problem}", file=sys.stderr)
            return 2
        print(f"[demo] 环境: {app_env}，数据库: {settings.database_url.split('@')[-1]}")
        print(f"[demo] 账号: teacher01 / admin01 / student01，统一密码 {DEMO_PASSWORD}")
        print("[demo] 已存在账号不会被改密；无法登录时请重建演示卷")
        for spec in DEMO_CONSUMABLES:
            consumable = db.scalars(
                select(Consumable).where(
                    Consumable.class_id == DEMO_CLASS_ID, Consumable.code == spec.code
                )
            ).one()
            print(f"[demo] {spec.code} {consumable.name}: {_tier_line(db, consumable)}")
        print("[demo] 完成；如需重建演示数据，删除隔离演示卷后重新启动并执行本命令")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
