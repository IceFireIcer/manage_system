from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    jwt_expire_minutes: int = 120

    llm_api_key: str = "sk-your-api-key-here"
    llm_base_url: str = "https://api.example.com/v1"
    llm_model: str = "your-model-name"


@lru_cache
def get_settings() -> Settings:
    return Settings()
