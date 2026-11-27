import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_SCRIPT = REPO_ROOT / "scripts" / "db-init" / "01-least-privilege-accounts.sh"
ROTATE_SCRIPT = REPO_ROOT / "scripts" / "db-rotate-credentials.sh"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DEPLOY_DOC = REPO_ROOT / "docs" / "deploy.md"

VALID_JWT_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
APP_URL = "mysql+pymysql://appuser:S3cureAppPassw0rd@db:3306/manage_system?charset=utf8mb4"
MIGRATE_URL = (
    "mysql+pymysql://labstock_migrator:S3cureMigratePassw0rd@db:3306/manage_system?charset=utf8mb4"
)

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="需要 bash 才能校验部署脚本")


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )


def _settings(**overrides) -> Settings:
    data = {
        "app_env": "prod",
        "jwt_secret_key": VALID_JWT_SECRET,
        "database_url": APP_URL,
        "migration_database_url": MIGRATE_URL,
    }
    data.update(overrides)
    return Settings(_env_file=None, **data)


def test_least_privilege_scripts_exist_and_pass_syntax_check() -> None:
    for script in (INIT_SCRIPT, ROTATE_SCRIPT):
        assert script.is_file(), f"缺少脚本：{script}"
        assert script.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/env bash"
        result = _run(["-n", script.as_posix()])
        assert result.returncode == 0, result.stderr


def test_app_account_is_granted_only_required_dml() -> None:
    content = INIT_SCRIPT.read_text(encoding="utf-8")

    assert "GRANT SELECT, INSERT, UPDATE ON" in content
    assert "GRANT ALL" not in content
    app_line = next(
        line for line in content.splitlines() if "GRANT SELECT, INSERT, UPDATE ON" in line
    )
    for forbidden in ("DELETE", "CREATE", "ALTER", "DROP", "GRANT OPTION"):
        assert forbidden not in app_line


def test_migration_and_backup_accounts_have_bounded_privileges() -> None:
    content = INIT_SCRIPT.read_text(encoding="utf-8")

    assert "MIGRATE_USER" in content and "labstock_migrator" in content
    assert "CREATE, ALTER, DROP, INDEX" in content
    assert "labstock_backup" in content
    assert "GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON" in content
    assert "GRANT PROCESS ON *.* TO" in content
    assert "REVOKE ALL PRIVILEGES, GRANT OPTION" in content


def test_init_script_dry_run_does_not_require_passwords() -> None:
    result = _run([INIT_SCRIPT.as_posix(), "--dry-run"])

    assert result.returncode == 0, result.stderr
    assert "SELECT/INSERT/UPDATE" in result.stdout
    assert "不会删除或重建数据库" in result.stdout


def test_rotation_script_preserves_data_and_verifies_ledger() -> None:
    content = ROTATE_SCRIPT.read_text(encoding="utf-8")

    assert "ALTER USER" in content
    assert "SELECT COUNT(*) FROM consumables" in content
    assert "compose down -v" not in content
    assert "DROP DATABASE" not in content
    assert "DROP TABLE" not in content
    assert "rm -rf" not in content


def test_rotation_dry_run_reports_no_volume_removal(tmp_path: Path) -> None:
    result = _run([ROTATE_SCRIPT.as_posix(), "--dry-run", "--env-file", str(tmp_path / ".env")])

    assert result.returncode == 0, result.stderr
    assert "ALTER USER" in result.stdout
    assert "不会执行 down -v" in result.stdout
    assert "consumables" in result.stdout


def test_compose_uses_dedicated_accounts_and_mounts_init_script() -> None:
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "DATABASE_URL: mysql+pymysql://${MYSQL_APP_USER" in content
    assert "MIGRATION_DATABASE_URL: mysql+pymysql://${MYSQL_MIGRATE_USER" in content
    assert "DATABASE_URL: mysql+pymysql://root:" not in content
    assert "./scripts/db-init:/docker-entrypoint-initdb.d:ro" in content
    assert "MYSQL_APP_PASSWORD" in content
    assert "MYSQL_MIGRATE_PASSWORD" in content
    assert "MYSQL_BACKUP_PASSWORD" in content


def test_env_example_and_docs_document_rotation() -> None:
    env_content = ENV_EXAMPLE.read_text(encoding="utf-8")
    doc_content = DEPLOY_DOC.read_text(encoding="utf-8")

    for key in ("MYSQL_APP_PASSWORD", "MYSQL_MIGRATE_PASSWORD", "MYSQL_BACKUP_PASSWORD"):
        assert f"{key}=" in env_content
        assert key in doc_content
    assert "### 5.3 数据库最小权限、凭据轮换与登录节流" in doc_content
    assert "保留已有数据" in doc_content
    assert "不执行 `down -v`" in doc_content


def test_production_accepts_rotated_password_for_same_database() -> None:
    rotated_url = APP_URL.replace("S3cureAppPassw0rd", "RotatedAppPassw0rd9")
    old_parts = urlsplit(APP_URL)
    new_parts = urlsplit(rotated_url)

    assert (old_parts.hostname, old_parts.port, old_parts.path) == (
        new_parts.hostname,
        new_parts.port,
        new_parts.path,
    )
    assert old_parts.password != new_parts.password

    settings = _settings(database_url=rotated_url)
    settings.validate_production()

    assert settings.database_credentials() == ("appuser", "RotatedAppPassw0rd9")
    assert settings.resolved_migration_database_url == MIGRATE_URL


def test_production_rejects_root_and_migration_placeholders() -> None:
    root_settings = _settings(database_url="mysql+pymysql://root:StrongPassw0rd@db:3306/manage_system")
    root_errors = root_settings.production_config_errors()
    assert any("root" in error for error in root_errors)

    placeholder_settings = _settings(
        migration_database_url=(
            "mysql+pymysql://labstock_migrator:change-me-migrate-password@db:3306/manage_system"
        )
    )
    assert "MIGRATION_DATABASE_URL 仍为占位凭据" in placeholder_settings.production_config_errors()


def test_migration_url_falls_back_to_application_url() -> None:
    settings = _settings(migration_database_url="")

    assert settings.resolved_migration_database_url == APP_URL
    settings.validate_production()


def test_backup_script_uses_backup_account() -> None:
    content = (REPO_ROOT / "scripts" / "db-backup.sh").read_text(encoding="utf-8")

    assert "MYSQL_BACKUP_PASSWORD" in content
    assert "mysqldump -u\"$BACKUP_USER\"" in content
    assert "mysql -uroot -p" not in content
