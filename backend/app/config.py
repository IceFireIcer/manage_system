from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32
JWT_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change-in-prod",
    "manage-system-default",
    "to-a-random-secret",
)
DATABASE_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "user:password",
    "managesystem2026",
)
PRODUCTION_ENVS = {"prod", "production"}
DATABASE_ROOT_DETAIL = "数据库连接不得使用 root 账号"


class ProductionConfigError(RuntimeError):
    """生产环境配置缺失或仍为占位值时抛出，用于拒绝启动。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "班级实训耗材智能管理系统"
    app_env: str = "dev"
    debug: bool = True

    database_url: str = "mysql+pymysql://user:password@127.0.0.1:3306/manage_system"
    migration_database_url: str = ""

    jwt_secret_key: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    login_throttle_account_max_attempts: int = 5
    login_throttle_account_window_seconds: int = 900
    login_throttle_source_max_attempts: int = 100
    login_throttle_source_window_seconds: int = 300
    login_throttle_block_seconds: int = 300

    auth_log_retention_days: int = 90
    auth_log_max_rows: int = 20000
    audit_log_retention_days: int = 0
    assistant_retention_days: int = 90

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    settings_encryption_key: str = ""

    cookie_secure: bool | None = None
    trusted_hosts: str = ""
    trusted_proxies: str = ""

    ops_backup_dir: str = ""
    ops_backup_max_age_hours: int = 36
    ops_disk_min_free_mb: int = 500
    ops_error_window_seconds: int = 300
    ops_error_alert_threshold: int = 5

    @field_validator("cookie_secure", mode="before")
    @classmethod
    def _blank_bool_as_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS

    @property
    def cookie_secure_effective(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production

    @property
    def trusted_host_set(self) -> set[str]:
        return {item.strip().lower() for item in self.trusted_hosts.split(",") if item.strip()}

    @property
    def trusted_proxy_set(self) -> set[str]:
        return {item.strip() for item in self.trusted_proxies.split(",") if item.strip()}

    def database_credentials(self) -> tuple[str, str]:
        parts = urlsplit(self.database_url)
        return parts.username or "", parts.password or ""

    @property
    def resolved_migration_database_url(self) -> str:
        return self.migration_database_url.strip() or self.database_url.strip()

    @staticmethod
    def _database_url_errors(label: str, url: str) -> list[str]:
        errors: list[str] = []
        if not url:
            errors.append(f"{label} 缺失")
            return errors
        parts = urlsplit(url)
        if not parts.password:
            errors.append(f"{label} 缺少数据库密码")
        if (parts.username or "").strip().lower() == "root":
            errors.append(f"{label} {DATABASE_ROOT_DETAIL}")
        if any(marker in url.lower() for marker in DATABASE_PLACEHOLDER_MARKERS):
            errors.append(f"{label} 仍为占位凭据")
        return errors

    def production_config_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.is_production:
            return errors

        jwt_secret = self.jwt_secret_key.strip()
        jwt_lower = jwt_secret.lower()
        if not jwt_secret:
            errors.append("JWT_SECRET_KEY 缺失")
        else:
            if len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
                errors.append(f"JWT_SECRET_KEY 长度不足 {MIN_JWT_SECRET_LENGTH} 位")
            if any(marker in jwt_lower for marker in JWT_PLACEHOLDER_MARKERS):
                errors.append("JWT_SECRET_KEY 仍为占位值")

        errors.extend(self._database_url_errors("DATABASE_URL", self.database_url.strip()))
        if self.migration_database_url.strip():
            errors.extend(
                self._database_url_errors(
                    "MIGRATION_DATABASE_URL", self.migration_database_url.strip()
                )
            )

        return errors

    def validate_production(self) -> None:
        errors = self.production_config_errors()
        if errors:
            raise ProductionConfigError("生产配置校验失败：" + "；".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
