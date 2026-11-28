import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.models import Consumable, Course, CourseRecipeSnapshot, InventoryEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
COURSE_TABLES = {
    "courses",
    "course_sessions",
    "course_recipe_snapshots",
    "course_recipe_items",
}


def _consumable(
    db: Session, name: str = "焊丝", unit: str = "卷", stock: int = 10
) -> Consumable:
    item = Consumable(
        name=name,
        spec="φ1.2mm",
        unit=unit,
        location="一号柜",
        safety_stock=0,
        current_stock=stock,
        class_id=1,
        status="enabled",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _course(client: TestClient, name: str = "焊接实训") -> dict:
    response = client.post(
        "/api/v1/courses",
        json={"name": name, "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_teacher_creates_and_lists_courses_with_pagination(teacher_client: TestClient) -> None:
    first = _course(teacher_client, "焊接实训")
    _course(teacher_client, "数控加工")
    _course(teacher_client, "电子装配")

    assert first["status"] == "enabled"
    assert first["session_count"] == 0
    assert first["recipe_version"] is None

    page_one = teacher_client.get("/api/v1/courses", params={"page": 1, "page_size": 2})
    assert page_one.status_code == 200
    body = page_one.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2

    page_two = teacher_client.get("/api/v1/courses", params={"page": 2, "page_size": 2})
    assert len(page_two.json()["items"]) == 1


def test_admin_can_maintain_course(admin_client: TestClient) -> None:
    created = _course(admin_client, "班委维护课程")
    updated = admin_client.put(
        f"/api/v1/courses/{created['id']}",
        json={"name": "班委维护课程（更新）", "status": "disabled"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "disabled"
    assert updated.json()["version"] == 2

    detail = admin_client.get(f"/api/v1/courses/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "班委维护课程（更新）"


def test_student_cannot_access_courses(student_client: TestClient) -> None:
    assert student_client.get("/api/v1/courses").status_code == 403
    assert student_client.post("/api/v1/courses", json={"name": "学生课程"}).status_code == 403
    assert student_client.get("/api/v1/courses/1/sessions").status_code == 403
    assert student_client.get("/api/v1/courses/1/recipes/current").status_code == 403


def test_course_is_scoped_to_class(
    teacher_client: TestClient, db_session: Session
) -> None:
    other = Course(
        class_id=2,
        name="其他班级课程",
        class_label="计算机 2401",
        semester="2026 秋",
        status="enabled",
        created_by_id=1,
        created_by_name="王老师",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    assert teacher_client.get(f"/api/v1/courses/{other.id}").status_code == 404
    assert teacher_client.get("/api/v1/courses").json()["total"] == 0


def test_duplicate_course_name_in_same_semester_rejected(teacher_client: TestClient) -> None:
    _course(teacher_client, "焊接实训")
    duplicate = teacher_client.post(
        "/api/v1/courses",
        json={"name": "焊接实训", "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert duplicate.status_code == 400
    assert "detail" in duplicate.json()


def test_sessions_bind_snapshot_and_keep_history(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    course_id = course["id"]

    first_recipe = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": [{"consumable_id": consumable.id, "quantity_per_person": 2}]},
    )
    assert first_recipe.status_code == 201, first_recipe.text
    assert first_recipe.json()["version"] == 1
    assert first_recipe.json()["items"][0]["unit"] == "卷"

    first_session = teacher_client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={"seq": 1, "session_date": "2026-12-01", "headcount": 42},
    )
    assert first_session.status_code == 201, first_session.text
    assert first_session.json()["recipe_version"] == 1
    assert first_session.json()["class_label"] == "计算机 2301"

    second_recipe = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": [{"consumable_id": consumable.id, "quantity_per_person": 3}]},
    )
    assert second_recipe.status_code == 201
    assert second_recipe.json()["version"] == 2

    second_session = teacher_client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={"seq": 2, "session_date": "2026-12-08", "headcount": 40},
    )
    assert second_session.json()["recipe_version"] == 2

    sessions = teacher_client.get(f"/api/v1/courses/{course_id}/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["total"] == 2
    assert [item["recipe_version"] for item in sessions.json()["items"]] == [1, 2]

    current = teacher_client.get(f"/api/v1/courses/{course_id}/recipes/current").json()
    assert current["configured"] is True
    assert current["snapshot"]["version"] == 2
    assert current["snapshot"]["items"][0]["quantity_per_person"] == 3

    history = teacher_client.get(f"/api/v1/courses/{course_id}/recipes").json()
    assert [snapshot["version"] for snapshot in history["items"]] == [2, 1]
    assert history["items"][1]["items"][0]["quantity_per_person"] == 2


def test_recipe_requires_consumable_unit_match(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, unit="卷")
    course_id = _course(teacher_client)["id"]

    mismatch = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={
            "items": [
                {
                    "consumable_id": consumable.id,
                    "quantity_per_person": 1,
                    "unit": "张",
                }
            ]
        },
    )
    assert mismatch.status_code == 400
    assert "单位" in mismatch.json()["detail"]

    accepted = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={
            "items": [
                {
                    "consumable_id": consumable.id,
                    "quantity_per_person": 1,
                    "unit": "卷",
                }
            ]
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["items"][0]["unit"] == "卷"


def test_recipe_rejects_more_than_three_or_duplicate_consumables(
    teacher_client: TestClient, db_session: Session
) -> None:
    items = [_consumable(db_session, name=f"耗材{i}", unit="个") for i in range(4)]
    course_id = _course(teacher_client)["id"]

    too_many = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={
            "items": [
                {"consumable_id": item.id, "quantity_per_person": 1}
                for item in items
            ]
        },
    )
    assert too_many.status_code == 422

    duplicate = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={
            "items": [
                {"consumable_id": items[0].id, "quantity_per_person": 1},
                {"consumable_id": items[0].id, "quantity_per_person": 2},
            ]
        },
    )
    assert duplicate.status_code == 422


def test_recipe_rejects_cross_class_consumable(
    teacher_client: TestClient, db_session: Session
) -> None:
    other = Consumable(
        name="外班耗材",
        spec="规格",
        unit="个",
        location="二号柜",
        safety_stock=0,
        current_stock=5,
        class_id=2,
        status="enabled",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    course_id = _course(teacher_client)["id"]
    response = teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": [{"consumable_id": other.id, "quantity_per_person": 1}]},
    )
    assert response.status_code == 400


def test_session_sequence_conflict_and_update(teacher_client: TestClient) -> None:
    course_id = _course(teacher_client)["id"]
    created = teacher_client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={"seq": 1, "session_date": "2026-12-01", "headcount": 40},
    )
    assert created.status_code == 201

    conflict = teacher_client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={"seq": 1, "session_date": "2026-12-02", "headcount": 40},
    )
    assert conflict.status_code == 400

    updated = teacher_client.put(
        f"/api/v1/courses/{course_id}/sessions/{created.json()['id']}",
        json={"headcount": 45, "status": "completed"},
    )
    assert updated.status_code == 200
    assert updated.json()["headcount"] == 45
    assert updated.json()["status"] == "completed"


def test_recipe_maintenance_does_not_touch_inventory(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=10)
    course_id = _course(teacher_client)["id"]

    teacher_client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": [{"consumable_id": consumable.id, "quantity_per_person": 2}]},
    )
    teacher_client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={"seq": 1, "session_date": "2026-12-01", "headcount": 40},
    )

    event_count = db_session.scalar(select(func.count()).select_from(InventoryEvent))
    snapshot_count = db_session.scalar(
        select(func.count()).select_from(CourseRecipeSnapshot)
    )
    db_session.refresh(consumable)
    assert event_count == 0
    assert snapshot_count == 1
    assert consumable.current_stock == 10

def _run_alembic(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_course_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "courses_migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}

    created = _run_alembic(env, "upgrade", "k6f7a8b9c0d1")
    assert created.returncode == 0, created.stderr
    assert COURSE_TABLES <= _tables(db_path)

    heads = _run_alembic(env, "heads")
    assert heads.returncode == 0
    head_lines = [line for line in heads.stdout.splitlines() if line.strip()]
    assert len(head_lines) == 1

    downgraded = _run_alembic(env, "downgrade", "j5e6f7a8b9c0")
    assert downgraded.returncode == 0, downgraded.stderr
    assert not COURSE_TABLES & _tables(db_path)

    upgraded = _run_alembic(env, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert COURSE_TABLES <= _tables(db_path)
