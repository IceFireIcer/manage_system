import pytest
from app.config import ProductionConfigError, Settings, get_settings
from app.main import create_app
from app.security import host_is_allowed, resolve_forwarded_scheme
from fastapi.testclient import TestClient

VALID_JWT_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
VALID_DATABASE_URL = (
    "mysql+pymysql://appuser:S3curePassw0rd@db:3306/manage_system?charset=utf8mb4"
)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def build_prod_settings(**overrides) -> Settings:
    data = {
        "app_env": "prod",
        "jwt_secret_key": VALID_JWT_SECRET,
        "database_url": VALID_DATABASE_URL,
    }
    data.update(overrides)
    return Settings(_env_file=None, **data)


def build_prod_client(monkeypatch, **env: str) -> TestClient:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET_KEY", VALID_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(create_app())


class TestProductionConfigValidation:
    def test_development_allows_placeholder_config(self) -> None:
        settings = Settings(_env_file=None)

        assert settings.is_production is False
        assert settings.production_config_errors() == []
        assert settings.cookie_secure_effective is False

    def test_production_accepts_valid_secrets(self) -> None:
        settings = build_prod_settings()

        assert settings.is_production is True
        assert settings.cookie_secure_effective is True
        settings.validate_production()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("jwt_secret_key", ""),
            ("jwt_secret_key", "change-me-to-a-random-secret"),
            ("jwt_secret_key", "short-secret"),
            ("database_url", ""),
            ("database_url", "mysql+pymysql://user:password@127.0.0.1:3306/manage_system"),
            ("database_url", "mysql+pymysql://root:ManageSystem2026@db:3306/manage_system"),
            ("database_url", "sqlite:///./prod.db"),
        ],
    )
    def test_production_rejects_missing_or_placeholder(self, field: str, value: str) -> None:
        settings = build_prod_settings(**{field: value})

        assert settings.production_config_errors()
        with pytest.raises(ProductionConfigError):
            settings.validate_production()

    def test_create_app_fails_fast_on_placeholder_jwt(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setenv("JWT_SECRET_KEY", "change-me-to-a-random-secret")
        monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
        get_settings.cache_clear()

        with pytest.raises(ProductionConfigError):
            create_app()

    def test_create_app_fails_fast_on_placeholder_database(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setenv("JWT_SECRET_KEY", VALID_JWT_SECRET)
        monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:password@127.0.0.1:3306/x")
        get_settings.cache_clear()

        with pytest.raises(ProductionConfigError):
            create_app()


class TestProductionHttpsGuard:
    def test_plain_http_rejected_in_production(self, monkeypatch) -> None:
        client = build_prod_client(monkeypatch)

        response = client.get("/api/v1/health")

        assert response.status_code == 400
        assert response.json()["detail"] == "生产环境必须通过 HTTPS 访问"

    def test_forwarded_https_accepted_in_production(self, monkeypatch) -> None:
        client = build_prod_client(monkeypatch)

        response = client.get("/api/v1/health", headers={"X-Forwarded-Proto": "https"})

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_untrusted_proxy_header_rejected(self, monkeypatch) -> None:
        client = build_prod_client(monkeypatch, TRUSTED_PROXIES="10.0.0.1")

        response = client.get("/api/v1/health", headers={"X-Forwarded-Proto": "https"})

        assert response.status_code == 400
        assert response.json()["detail"] == "不可信代理来源"


    def test_trusted_host_allowlist_enforced(self, monkeypatch) -> None:
        client = build_prod_client(monkeypatch, TRUSTED_HOSTS="lab.example.edu")

        blocked = client.get(
            "/api/v1/health",
            headers={"X-Forwarded-Proto": "https", "Host": "evil.example"},
        )
        allowed = client.get(
            "/api/v1/health",
            headers={"X-Forwarded-Proto": "https", "Host": "lab.example.edu"},
        )

        assert blocked.status_code == 400
        assert blocked.json()["detail"] == "非法来源主机"
        assert allowed.status_code == 200


class TestProductionCookieAttributes:
    def test_login_cookie_secure_in_production(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr("app.routers.auth.get_settings", lambda: build_prod_settings())

        response = client.post(
            "/api/v1/auth/login", json={"account": "teacher01", "password": "Teacher@123"}
        )

        assert response.status_code == 200
        cookie = response.headers.get("set-cookie", "").lower()
        assert "access_token=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "; secure" in cookie

    def test_login_cookie_not_secure_in_development(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"account": "teacher01", "password": "Teacher@123"}
        )

        assert response.status_code == 200
        cookie = response.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie
        assert "; secure" not in cookie


class TestProxyHelpers:
    def test_resolve_forwarded_scheme_uses_trusted_header(self) -> None:
        scheme, error = resolve_forwarded_scheme("http", "https", "172.18.0.5", {"172.16.0.0/12"})

        assert (scheme, error) == ("https", None)

    def test_resolve_forwarded_scheme_ignores_untrusted_header(self) -> None:
        scheme, error = resolve_forwarded_scheme("http", "https", "203.0.113.9", {"172.16.0.0/12"})

        assert scheme == "http"
        assert error == "不可信代理来源"

    def test_host_is_allowed_matches_name_and_port(self) -> None:
        assert host_is_allowed("lab.example.edu:443", {"lab.example.edu"}) is True
        assert host_is_allowed("evil.example", {"lab.example.edu"}) is False
        assert host_is_allowed("anything", set()) is True
