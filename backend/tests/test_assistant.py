from datetime import timedelta

import pytest
from app import business_time
from app.assistant.context import build_context
from app.assistant.service import trim_threads
from app.assistant.tools import ToolActionError, ToolContext, prepare_action
from app.config import Settings
from app.models import AssistantThread, Consumable, User
from fastapi.testclient import TestClient
from sqlalchemy import select


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def test_trim_threads_respects_retention(db_session) -> None:
    teacher_id = _user_id(db_session, "teacher01")
    now = business_time.now()
    db_session.add_all(
        [
            AssistantThread(
                user_id=teacher_id,
                class_id=1,
                title="旧会话",
                last_message_at=now - timedelta(days=91),
            ),
            AssistantThread(
                user_id=teacher_id,
                class_id=1,
                title="新会话",
                last_message_at=now - timedelta(days=89),
            ),
        ]
    )
    db_session.commit()

    deleted = trim_threads(
        db_session, Settings(_env_file=None, assistant_retention_days=90)
    )
    db_session.commit()

    assert deleted == 1
    assert db_session.scalars(select(AssistantThread.title)).all() == ["新会话"]
    assert (
        trim_threads(
            db_session, Settings(_env_file=None, assistant_retention_days=0)
        )
        == 0
    )


def test_threads_are_isolated_by_user(admin_client: TestClient) -> None:
    created = admin_client.post(
        "/api/v1/assistant/threads", json={"title": "班委会话"}
    )
    assert created.status_code == 201, created.text
    thread_id = created.json()["id"]

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "teacher01", "Teacher@123")

    listed = admin_client.get("/api/v1/assistant/threads")
    assert listed.status_code == 200
    assert listed.json() == []
    assert (
        admin_client.get(f"/api/v1/assistant/threads/{thread_id}").status_code == 404
    )


def test_status_reports_not_configured(teacher_client: TestClient) -> None:
    response = teacher_client.get("/api/v1/assistant/status")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert "未配置" in data["message"]

def test_context_sanitizes_sensitive_values(db_session) -> None:
    context = build_context(
        db_session,
        role="teacher",
        class_id=1,
        raw={
            "route": "/records?phone=13800000000&token=abc",
            "page": "student_no",
            "active_role": "super_admin",
            "selected_entity": {
                "type": "consumable",
                "id": 1,
                "name": "焊丝",
                "phone": "13800000000",
                "token": "abc",
            },
        },
    )

    assert context["route"] == "/records"
    assert context["page"] is None
    assert context["active_role"] == "teacher"
    assert context["selected_entity"] == {
        "type": "consumable",
        "id": 1,
        "name": "焊丝",
    }


def test_prefill_tools_return_expected_routes(db_session) -> None:
    teacher = db_session.scalars(
        select(User).where(User.account == "teacher01")
    ).one()
    consumable = Consumable(
        name="焊丝",
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=5,
        current_stock=10,
        class_id=1,
    )
    db_session.add(consumable)
    db_session.commit()
    ctx = ToolContext(db=db_session, user=teacher, role="teacher", class_id=1)

    _, _, count = prepare_action(
        "prefill_inventory_count", ctx, {"consumable_id": consumable.id}
    )
    assert count["route"] == "/overview"
    assert count["query"] == {
        "focus": "inventory_count",
        "consumable_id": str(consumable.id),
    }

    _, _, course = prepare_action(
        "prefill_course_recipe",
        ctx,
        {"course_id": 3, "consumable_id": consumable.id},
    )
    assert course["route"] == "/courses"
    assert course["query"] == {
        "course_id": "3",
        "consumable_id": str(consumable.id),
    }

    student = db_session.scalars(
        select(User).where(User.account == "student01")
    ).one()
    student_ctx = ToolContext(
        db=db_session, user=student, role="student", class_id=1
    )
    with pytest.raises(ToolActionError):
        prepare_action(
            "prefill_inventory_count",
            student_ctx,
            {"consumable_id": consumable.id},
        )
