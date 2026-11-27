from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pytest
from app import inventory_ledger
from app.models import Base, Consumable, InventoryEvent, Requisition, RequisitionOrder, User
from app.restock import create_restock_order
from app.routers.requisitions import _create_requisition_impl, reverse_requisition
from app.schemas_requisitions import (
    RequisitionCreate,
    RequisitionCreateOut,
    RequisitionItemIn,
    ReversalCreate,
)
from app.schemas_restock import RestockItemIn
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

MYSQL_URL = os.getenv("LABSTOCK_MYSQL_TEST_URL", "").strip()
SKIP_REASON = (
    "需要真实 MySQL：设置 LABSTOCK_MYSQL_TEST_URL，"
    "或运行 scripts/acceptance/run-mysql-concurrency.sh"
)
pytestmark = pytest.mark.skipif(not MYSQL_URL, reason=SKIP_REASON)


@dataclass
class Outcome:
    value: Any = None
    error: Exception | None = None
    attempts: int = 1


@pytest.fixture()
def engine():
    database = urlsplit(MYSQL_URL).path.lstrip("/")
    if "test" not in database and "concurrency" not in database:
        pytest.skip("测试库名必须包含 test 或 concurrency，防止误连正式库")
    created = create_engine(
        MYSQL_URL,
        pool_pre_ping=True,
        connect_args={"init_command": "SET SESSION innodb_lock_wait_timeout=3"},
    )
    Base.metadata.drop_all(created)
    Base.metadata.create_all(created)
    try:
        yield created
    finally:
        created.dispose()


def _seed(engine, stock_a: int, stock_b: int) -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as db:
        admin = User(
            account=f"admin-{suffix}",
            password_hash="x",
            name="并发班委",
            role="admin",
            status="enabled",
            must_change_password=False,
        )
        student = User(
            account=f"student-{suffix}",
            password_hash="x",
            name="并发学生",
            role="student",
            status="enabled",
            must_change_password=False,
        )
        db.add_all([admin, student])
        db.flush()
        consumable_a = Consumable(
            code=f"CA-{suffix}",
            name="并发耗材 A",
            unit="件",
            safety_stock=0,
            current_stock=stock_a,
            status="enabled",
        )
        consumable_b = Consumable(
            code=f"CB-{suffix}",
            name="并发耗材 B",
            unit="件",
            safety_stock=0,
            current_stock=stock_b,
            status="enabled",
        )
        db.add_all([consumable_a, consumable_b])
        db.flush()
        inventory_ledger.record_opening(db, consumable_a, admin)
        inventory_ledger.record_opening(db, consumable_b, admin)
        db.commit()
        return {
            "admin": admin.id,
            "student": student.id,
            "a": consumable_a.id,
            "b": consumable_b.id,
        }


def _payload(
    ids: dict[str, int],
    items: list[tuple[int, int]],
    request_id: str | None = None,
) -> RequisitionCreate:
    return RequisitionCreate(
        items=[
            RequisitionItemIn(consumable_id=consumable_id, quantity=quantity)
            for consumable_id, quantity in items
        ],
        user_id=ids["student"],
        request_id=request_id or f"mysql-{uuid.uuid4().hex}",
    )


def _issue(db: Session, payload: RequisitionCreate, admin_id: int):
    admin = db.get(User, admin_id)
    return _create_requisition_impl(
        db, payload, admin, max_backfill_days=30, controlled=False
    )


def _attempt(
    engine,
    action: Callable[[Session], Any],
    *,
    allow_retry: bool = True,
) -> Outcome:
    attempts = 0
    while True:
        attempts += 1
        try:
            with Session(engine) as db:
                return Outcome(value=action(db), attempts=attempts)
        except HTTPException as exc:
            retryable = (
                exc.status_code == 503
                and exc.headers.get("X-Error-Code") == "retry_required"
            )
            if allow_retry and retryable and attempts < 2:
                time.sleep(0.3)
                continue
            return Outcome(error=exc, attempts=attempts)
        except Exception as exc:
            return Outcome(error=exc, attempts=attempts)


def _run_concurrently(engine, actions: list[Callable[[Session], Any]]) -> list[Outcome]:
    barrier = threading.Barrier(len(actions))
    results: list[Outcome | None] = [None] * len(actions)

    def worker(index: int, action: Callable[[Session], Any]) -> None:
        try:
            barrier.wait(timeout=15)
            results[index] = _attempt(engine, action)
        except Exception as exc:
            results[index] = Outcome(error=exc)

    threads = [
        threading.Thread(target=worker, args=(index, action), daemon=True)
        for index, action in enumerate(actions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "并发线程未在 60 秒内结束"
    assert all(result is not None for result in results)
    return [result for result in results if result is not None]


def _ledger(engine, consumable_id: int) -> tuple[int, list[InventoryEvent]]:
    with Session(engine) as db:
        consumable = db.get(Consumable, consumable_id)
        assert consumable is not None
        events = list(
            db.scalars(
                select(InventoryEvent)
                .where(InventoryEvent.consumable_id == consumable_id)
                .order_by(InventoryEvent.exec_seq)
            ).all()
        )
        return consumable.current_stock, events


def _assert_ledger_consistent(engine, consumable_id: int, expected_stock: int) -> None:
    stock, events = _ledger(engine, consumable_id)
    assert stock == expected_stock
    assert [event.exec_seq for event in events] == list(range(1, len(events) + 1))
    for event in events:
        assert event.stock_after == event.stock_before + event.delta
    assert events[-1].stock_after == stock


def _count(engine, model) -> int:
    with Session(engine) as db:
        return len(db.scalars(select(model)).all())


def test_reverse_order_requisitions_finish_without_deadlock(engine) -> None:
    ids = _seed(engine, 20, 20)
    first = _payload(ids, [(ids["a"], 5), (ids["b"], 5)])
    second = _payload(ids, [(ids["b"], 5), (ids["a"], 5)])

    results = _run_concurrently(
        engine,
        [
            lambda db: _issue(db, first, ids["admin"]),
            lambda db: _issue(db, second, ids["admin"]),
        ],
    )

    assert all(result.error is None for result in results), results
    assert all(isinstance(result.value, RequisitionCreateOut) for result in results)
    _assert_ledger_consistent(engine, ids["a"], 10)
    _assert_ledger_consistent(engine, ids["b"], 10)
    assert _count(engine, Requisition) == 4
    with Session(engine) as db:
        orders = db.scalars(select(RequisitionOrder)).all()
        assert {order.status for order in orders} == {"submitted"}


def test_reverse_order_without_enough_stock_leaves_no_partial_order(engine) -> None:
    ids = _seed(engine, 5, 5)
    first = _payload(ids, [(ids["a"], 4), (ids["b"], 4)])
    second = _payload(ids, [(ids["b"], 4), (ids["a"], 4)])

    results = _run_concurrently(
        engine,
        [
            lambda db: _issue(db, first, ids["admin"]),
            lambda db: _issue(db, second, ids["admin"]),
        ],
    )

    successes = [result for result in results if result.error is None]
    failures = [result for result in results if result.error is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, HTTPException)
    assert failures[0].error.status_code == 400
    _assert_ledger_consistent(engine, ids["a"], 1)
    _assert_ledger_consistent(engine, ids["b"], 1)
    assert _count(engine, Requisition) == 2
    with Session(engine) as db:
        statuses = sorted(order.status for order in db.scalars(select(RequisitionOrder)).all())
        assert statuses == ["rejected", "submitted"]
        rejected = db.scalars(
            select(RequisitionOrder).where(RequisitionOrder.status == "rejected")
        ).all()
        assert len(rejected) == 1
        assert rejected[0].receipt_json is None
        assert any(
            event.source_type == "requisition_order"
            for event in db.scalars(select(InventoryEvent)).all()
        )


def test_restock_and_reversal_in_parallel_keep_ledger_balance(engine) -> None:
    ids = _seed(engine, 5, 5)
    with Session(engine) as db:
        created = _issue(db, _payload(ids, [(ids["a"], 5)]), ids["admin"])
    assert isinstance(created, RequisitionCreateOut)
    requisition_id = created.receipt.lines[0].requisition_id
    restock_request = f"rs-{uuid.uuid4().hex}"

    def restock(db: Session):
        admin = db.get(User, ids["admin"])
        return create_restock_order(
            db,
            items=[RestockItemIn(consumable_id=ids["a"], quantity=3)],
            operator=admin,
            request_id=restock_request,
        )

    def reversal(db: Session):
        admin = db.get(User, ids["admin"])
        return reverse_requisition(
            requisition_id, ReversalCreate(reason="并发红冲"), db, admin
        )

    results = _run_concurrently(engine, [restock, reversal])

    assert all(result.error is None for result in results), results
    _assert_ledger_consistent(engine, ids["a"], 8)
    with Session(engine) as db:
        normal = db.get(Requisition, requisition_id)
        assert normal is not None
        assert normal.status == "reversed"
        reversal_record = db.scalars(
            select(Requisition).where(Requisition.reversal_of == requisition_id)
        ).one()
        assert reversal_record.quantity == 5
        assert normal.id != reversal_record.id


def test_duplicate_request_id_concurrently_posts_once(engine) -> None:
    ids = _seed(engine, 5, 5)
    request_id = f"dup-{uuid.uuid4().hex}"
    first = _payload(ids, [(ids["a"], 1)], request_id=request_id)
    second = _payload(ids, [(ids["a"], 1)], request_id=request_id)

    results = _run_concurrently(
        engine,
        [
            lambda db: _issue(db, first, ids["admin"]),
            lambda db: _issue(db, second, ids["admin"]),
        ],
    )

    assert all(result.error is None for result in results), results
    created = [result for result in results if isinstance(result.value, RequisitionCreateOut)]
    repeated = [result for result in results if isinstance(result.value, JSONResponse)]
    assert len(created) == 1
    assert len(repeated) <= 1
    _assert_ledger_consistent(engine, ids["a"], 4)
    assert _count(engine, RequisitionOrder) == 1
    assert _count(engine, Requisition) == 1


def test_lost_receipt_retry_returns_same_receipt_without_double_posting(engine) -> None:
    ids = _seed(engine, 5, 5)
    request_id = f"lost-{uuid.uuid4().hex}"
    payload = _payload(ids, [(ids["a"], 1)], request_id=request_id)

    first = _attempt(engine, lambda db: _issue(db, payload, ids["admin"]))
    assert first.error is None
    assert isinstance(first.value, RequisitionCreateOut)

    retry = _attempt(engine, lambda db: _issue(db, payload, ids["admin"]))

    assert retry.error is None
    assert isinstance(retry.value, JSONResponse)
    assert retry.value.status_code == 200
    body = json.loads(retry.value.body)
    assert body["request_id"] == request_id
    assert body["receipt"]["order_no"] == first.value.order_no
    assert body["receipt"]["lines"][0]["quantity"] == 1
    _assert_ledger_consistent(engine, ids["a"], 4)
    assert _count(engine, RequisitionOrder) == 1
    assert _count(engine, Requisition) == 1


def test_lock_conflict_returns_retryable_result_and_retry_posts_once(engine) -> None:
    ids = _seed(engine, 10, 10)
    request_id = f"lock-{uuid.uuid4().hex}"
    payload = _payload(ids, [(ids["a"], 1)], request_id=request_id)

    lock_db = Session(engine)
    try:
        lock_db.execute(
            select(Consumable).where(Consumable.id == ids["a"]).with_for_update()
        )
        blocked = _attempt(
            engine, lambda db: _issue(db, payload, ids["admin"]), allow_retry=False
        )
    finally:
        lock_db.rollback()
        lock_db.close()

    assert isinstance(blocked.error, HTTPException)
    assert blocked.error.status_code == 503
    assert blocked.error.headers.get("X-Error-Code") == "retry_required"
    assert blocked.error.headers.get("Retry-After")
    _assert_ledger_consistent(engine, ids["a"], 10)
    assert _count(engine, RequisitionOrder) == 0

    retry = _attempt(engine, lambda db: _issue(db, payload, ids["admin"]))

    assert retry.error is None
    assert isinstance(retry.value, RequisitionCreateOut)
    _assert_ledger_consistent(engine, ids["a"], 9)
    assert _count(engine, RequisitionOrder) == 1
    assert _count(engine, Requisition) == 1
