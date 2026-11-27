import bcrypt
import pytest
from app.login_throttle import LoginThrottle, ThrottleLimits
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

FAST_PASSWORD = "Fast@123"
FAST_HASH = bcrypt.hashpw(FAST_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def build_throttle(
    *,
    account_max: int = 3,
    account_window: int = 900,
    source_max: int = 100,
    source_window: int = 300,
    block: int = 120,
    clock: FakeClock | None = None,
) -> tuple[LoginThrottle, FakeClock]:
    fake = clock or FakeClock()
    limits = ThrottleLimits(
        account_max_attempts=account_max,
        account_window_seconds=account_window,
        source_max_attempts=source_max,
        source_window_seconds=source_window,
        block_seconds=block,
    )
    return LoginThrottle(lambda: limits, clock=fake), fake


def add_user(db_session, account: str, password: str = FAST_PASSWORD) -> None:
    db_session.add(
        User(
            account=account,
            password_hash=FAST_HASH,
            name=account,
            role="student",
            status="enabled",
        )
    )
    db_session.commit()


def login(client: TestClient, account: str, password: str, headers: dict | None = None):
    return client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": password},
        headers=headers or {},
    )


def test_account_failures_block_and_log_once() -> None:
    throttle, clock = build_throttle(account_max=3, source_max=100)

    for _ in range(3):
        assert throttle.check("teacher01", "10.0.0.5") == (0, False)
        throttle.record_failure("teacher01", "10.0.0.5")

    retry_after, should_log = throttle.check("teacher01", "10.0.0.5")
    assert retry_after == 120
    assert should_log is True
    assert throttle.check("teacher01", "10.0.0.5") == (retry_after, False)
    assert throttle.check("teacher01", "10.0.0.6") == (retry_after, False)

    clock.advance(121)
    assert throttle.check("teacher01", "10.0.0.5") == (0, False)


def test_success_clears_account_failure_window() -> None:
    throttle, _ = build_throttle(account_max=3)

    throttle.record_failure("student01", "10.0.0.5")
    throttle.record_failure("student01", "10.0.0.5")
    throttle.record_success("student01", "10.0.0.5")
    throttle.record_failure("student01", "10.0.0.5")
    throttle.record_failure("student01", "10.0.0.5")

    assert throttle.check("student01", "10.0.0.5") == (0, False)


def test_old_failures_expire_outside_window() -> None:
    throttle, clock = build_throttle(account_max=3, account_window=60, block=30)

    throttle.record_failure("teacher01", "10.0.0.5")
    throttle.record_failure("teacher01", "10.0.0.5")
    clock.advance(61)
    throttle.record_failure("teacher01", "10.0.0.5")

    assert throttle.check("teacher01", "10.0.0.5") == (0, False)


def test_source_failures_block_independently_of_account() -> None:
    throttle, _ = build_throttle(account_max=100, source_max=3)

    for account in ("nobody1", "nobody2", "nobody3"):
        throttle.record_failure(account, "203.0.113.9")

    retry_after, _ = throttle.check("teacher01", "203.0.113.9")
    assert retry_after == 120
    assert throttle.check("teacher01", "203.0.113.10") == (0, False)


def test_throttle_state_is_bounded_by_max_keys() -> None:
    throttle, _ = build_throttle(account_max=1, source_max=100)
    throttle._max_keys = 4

    for index in range(20):
        throttle.record_failure(f"user{index}", f"source{index}")

    assert len(throttle._accounts) <= 4
    assert len(throttle._sources) <= 4


@pytest.fixture()
def fast_user(db_session):
    add_user(db_session, "fast01")


def test_repeated_failures_are_throttled_for_account(
    client: TestClient, fast_user, monkeypatch
) -> None:
    monkeypatch.setenv("LOGIN_THROTTLE_ACCOUNT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_THROTTLE_SOURCE_MAX_ATTEMPTS", "100")
    monkeypatch.setenv("LOGIN_THROTTLE_BLOCK_SECONDS", "120")

    for _ in range(3):
        assert login(client, "fast01", "wrong-password").status_code == 401

    blocked = login(client, "fast01", FAST_PASSWORD)

    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "登录尝试过于频繁，请稍后重试"
    assert int(blocked.headers["retry-after"]) > 0


def test_blocked_storm_does_not_grow_auth_logs_unbounded(
    client: TestClient, fast_user, db_session, monkeypatch
) -> None:
    monkeypatch.setenv("LOGIN_THROTTLE_ACCOUNT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_THROTTLE_SOURCE_MAX_ATTEMPTS", "100")
    monkeypatch.setenv("LOGIN_THROTTLE_BLOCK_SECONDS", "120")

    for _ in range(3):
        login(client, "fast01", "wrong-password")
    for _ in range(10):
        login(client, "fast01", FAST_PASSWORD)

    count = db_session.scalar(
        select(func.count()).select_from(OperationLog).where(OperationLog.action == "login")
    )
    assert count == 4


def test_source_limit_blocks_but_forwarded_source_can_continue(
    client: TestClient, fast_user, monkeypatch
) -> None:
    monkeypatch.setenv("LOGIN_THROTTLE_ACCOUNT_MAX_ATTEMPTS", "100")
    monkeypatch.setenv("LOGIN_THROTTLE_SOURCE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_THROTTLE_BLOCK_SECONDS", "120")
    monkeypatch.setenv("TRUSTED_PROXIES", "testclient")

    for index in range(3):
        assert login(client, f"nobody{index}", "whatever").status_code == 401

    blocked = login(client, "fast01", FAST_PASSWORD)
    assert blocked.status_code == 429

    forwarded = login(
        client,
        "fast01",
        FAST_PASSWORD,
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert forwarded.status_code == 200


def test_whole_class_normal_login_completes(client: TestClient, db_session) -> None:
    for index in range(40):
        add_user(db_session, f"class01_{index:02d}")

    for index in range(40):
        response = login(client, f"class01_{index:02d}", FAST_PASSWORD)
        assert response.status_code == 200, response.text

    assert login(client, "class01_00", FAST_PASSWORD).status_code == 200
