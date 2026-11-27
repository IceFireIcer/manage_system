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

    jwt_secret_key: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    cookie_secure: bool | None = None
    trusted_hosts: str = ""
    trusted_proxies: str = ""

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

        database_url = self.database_url.strip()
        if not database_url:
            errors.append("DATABASE_URL 缺失")
        else:
            _, password = self.database_credentials()
            if not password:
                errors.append("DATABASE_URL 缺少数据库密码")
            database_lower = database_url.lower()
            if any(marker in database_lower for marker in DATABASE_PLACEHOLDER_MARKERS):
                errors.append("DATABASE_URL 仍为占位凭据")

        return errors

    def validate_production(self) -> None:
        errors = self.production_config_errors()
        if errors:
            raise ProductionConfigError("生产配置校验失败：" + "；".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
