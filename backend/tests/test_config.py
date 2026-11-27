from app.config import Settings, get_settings


def test_settings_loadable_with_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "dev"
    assert settings.jwt_algorithm == "HS256"
    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.jwt_expire_hours == 8


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
