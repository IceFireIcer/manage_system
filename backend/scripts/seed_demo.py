"""演示数据初始化脚本（T11）。

在预置的 5 类耗材与三角色账号之上，构造演示所需的三级巡检场景：

- 焊丝：当前 1 卷、安全线 5、近 7 天净消耗 8 卷 -> 紧急
- 记号笔：当前 3 支、安全线 8、近 7 天净消耗 7 支 -> 一般
- 电阻：当前 250 个、安全线 200、近 7 天 100 个、前 7 天 40 个 -> 观察（趋势）
- 砂纸 / 实训导线：保持高于安全线且无近 7 天用量 -> 正常（不出建议）

同时写入 20 笔近 30 天历史领用流水（领用人轮换 student01/teacher01/admin01），
供记录查询与消耗汇总 7/30 天切换演示。流水按时间正序生成，stock_after 与实际
库存逐笔自洽。

幂等：以流水备注 ``[demo-seed]`` 作为标记；标记已存在时直接跳过，不重复写入、
不重复扣减库存。首次执行会拒绝在已有领用流水的库上运行，避免破坏可反推账目。

用法（在 backend 目录执行，需先 alembic upgrade head）：

    python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Consumable, Requisition, User  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

DEMO_MARKER = "[demo-seed]"
CLASS_ID = 1
OPERATOR_ACCOUNTS = ("student01", "teacher01", "admin01")

STATUS_ENABLED = "enabled"


@dataclass(frozen=True)
class DemoScenario:
    name: str
    spec: str
    unit: str
    location: str
    safety_stock: int
    target_stock: int


@dataclass(frozen=True)
class DemoFlow:
    name: str
    days_ago: int
    at: str
    quantity: int


DEMO_SCENARIOS = (
    DemoScenario("焊丝", "φ1.0mm", "卷", "实训室一号柜", 5, 1),
    DemoScenario("记号笔", "黑色", "支", "实训室一号柜", 8, 3),
    DemoScenario("电阻", "10kΩ 1/4W", "个", "实训室一号柜", 200, 250),
    DemoScenario("砂纸", "240目", "张", "实训室一号柜", 20, 50),
    DemoScenario("实训导线", "1mm² 红", "根", "实训室一号柜", 40, 100),
)

# 20 笔历史流水。第 1-6 天前落在近 7 天窗口；第 8-12 天前落在前 7 天窗口；
# 第 16-24 天前只进入 30 天窗口，保证砂纸/导线在 7 天内无用量且不触发趋势。
DEMO_FLOWS = (
    DemoFlow("砂纸", 24, "15:00", 5),
    DemoFlow("实训导线", 22, "16:00", 5),
    DemoFlow("砂纸", 18, "15:00", 5),
    DemoFlow("实训导线", 16, "16:00", 5),
    DemoFlow("电阻", 12, "14:00", 20),
    DemoFlow("焊丝", 11, "10:00", 2),
    DemoFlow("记号笔", 10, "11:00", 2),
    DemoFlow("焊丝", 9, "10:00", 2),
    DemoFlow("电阻", 9, "14:00", 20),
    DemoFlow("焊丝", 6, "09:00", 2),
    DemoFlow("记号笔", 6, "11:00", 2),
    DemoFlow("电阻", 6, "14:00", 25),
    DemoFlow("焊丝", 5, "10:00", 2),
    DemoFlow("电阻", 5, "14:00", 25),
    DemoFlow("记号笔", 4, "11:00", 3),
    DemoFlow("焊丝", 3, "10:00", 2),
    DemoFlow("记号笔", 2, "11:00", 2),
    DemoFlow("电阻", 2, "14:00", 25),
    DemoFlow("焊丝", 1, "10:00", 2),
    DemoFlow("电阻", 1, "14:00", 25),
)

DEMO_TARGETS = {scenario.name: scenario.target_stock for scenario in DEMO_SCENARIOS}


@dataclass(frozen=True)
class SeedResult:
    seeded: bool
    requisitions: int
    consumables: int


def _load_operators(db: Session) -> list[User]:
    users = {
        user.account: user
        for user in db.scalars(
            select(User).where(
                User.account.in_(OPERATOR_ACCOUNTS),
                User.status == STATUS_ENABLED,
            )
        ).all()
    }
    missing = [account for account in OPERATOR_ACCOUNTS if account not in users]
    if missing:
        raise RuntimeError(
            "缺少演示账号：" + "、".join(missing) + "，请先执行 alembic upgrade head"
        )
    return [users[account] for account in OPERATOR_ACCOUNTS]


def _ensure_consumables(db: Session) -> dict[str, Consumable]:
    existing = {
        consumable.name: consumable
        for consumable in db.scalars(
            select(Consumable).where(
                Consumable.class_id == CLASS_ID,
                Consumable.name.in_(tuple(DEMO_TARGETS)),
            )
        ).all()
    }
    created: dict[str, Consumable] = {}
    for scenario in DEMO_SCENARIOS:
        consumable = existing.get(scenario.name)
        if consumable is None:
            consumable = Consumable(
                name=scenario.name,
                spec=scenario.spec,
                unit=scenario.unit,
                location=scenario.location,
                safety_stock=scenario.safety_stock,
                current_stock=scenario.target_stock,
                class_id=CLASS_ID,
                status=STATUS_ENABLED,
            )
            db.add(consumable)
        created[scenario.name] = consumable
    db.flush()
    return created


def _to_datetime(now: datetime, days_ago: int, at: str) -> datetime:
    hour, minute = (int(part) for part in at.split(":"))
    return (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def seed_demo(db: Session) -> SeedResult:
    marker_exists = db.scalar(
        select(Requisition.id).where(Requisition.remark == DEMO_MARKER).limit(1)
    )
    if marker_exists is not None:
        db.rollback()
        return SeedResult(seeded=False, requisitions=0, consumables=0)

    now = datetime.now().replace(microsecond=0)
    operators = _load_operators(db)
    consumables = _ensure_consumables(db)

    existing_flows = (
        db.scalar(
            select(func.count())
            .select_from(Requisition)
            .where(Requisition.consumable_id.in_([item.id for item in consumables.values()]))
        )
        or 0
    )
    if existing_flows > 0:
        db.rollback()
        raise RuntimeError(
            "检测到演示耗材已有领用流水，无法保证 stock_after 与库存自洽；"
            "请在仅含预置数据的演示库上执行本脚本"
        )

    totals: dict[str, int] = {}
    for flow in DEMO_FLOWS:
        totals[flow.name] = totals.get(flow.name, 0) + flow.quantity

    for scenario in DEMO_SCENARIOS:
        consumable = consumables[scenario.name]
        consumable.spec = scenario.spec
        consumable.unit = scenario.unit
        consumable.location = scenario.location
        consumable.safety_stock = scenario.safety_stock
        consumable.current_stock = scenario.target_stock + totals.get(scenario.name, 0)
        consumable.status = STATUS_ENABLED
        consumable.updated_at = now
    db.flush()

    running = {
        scenario.name: consumables[scenario.name].current_stock
        for scenario in DEMO_SCENARIOS
    }
    ordered_flows = sorted(DEMO_FLOWS, key=lambda flow: (-flow.days_ago, flow.at))
    for index, flow in enumerate(ordered_flows):
        running[flow.name] -= flow.quantity
        db.add(
            Requisition(
                consumable_id=consumables[flow.name].id,
                user_id=operators[index % len(operators)].id,
                quantity=flow.quantity,
                stock_after=running[flow.name],
                type="normal",
                status="active",
                class_id=CLASS_ID,
                created_at=_to_datetime(now, flow.days_ago, flow.at),
                remark=DEMO_MARKER,
            )
        )

    final_stocks = {name: running[name] for name in DEMO_TARGETS}
    if final_stocks != DEMO_TARGETS:
        db.rollback()
        raise RuntimeError(f"演示数据库存计算异常：{final_stocks}")

    for name, stock in final_stocks.items():
        consumables[name].current_stock = stock
    db.flush()
    db.commit()
    return SeedResult(
        seeded=True,
        requisitions=len(ordered_flows),
        consumables=len(DEMO_SCENARIOS),
    )


def main() -> int:
    with SessionLocal() as db:
        try:
            result = seed_demo(db)
        except RuntimeError as exc:
            print(f"[seed-demo] 失败：{exc}")
            return 1

    if result.seeded:
        print(
            f"[seed-demo] 完成：写入 {result.requisitions} 笔历史流水，"
            f"校准 {result.consumables} 类耗材库存"
        )
        print("[seed-demo] 预期巡检结果：焊丝=紧急、记号笔=一般、电阻=观察；砂纸/导线正常")
    else:
        print("[seed-demo] 演示流水已存在，跳过（幂等）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
