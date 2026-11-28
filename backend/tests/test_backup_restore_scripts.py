import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "db-backup.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "db-restore-rehearsal.sh"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="需要 bash 才能校验部署脚本")


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_scripts_exist_and_have_shebang() -> None:
    for script in (BACKUP_SCRIPT, RESTORE_SCRIPT):
        assert script.is_file(), f"缺少脚本：{script}"
        first_line = script.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "#!/usr/bin/env bash"


@pytest.mark.parametrize("script", [BACKUP_SCRIPT, RESTORE_SCRIPT])
def test_script_passes_bash_syntax_check(script: Path) -> None:
    result = _run(["-n", script.as_posix()])
    assert result.returncode == 0, result.stderr


def test_backup_dry_run_reports_resolved_paths(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    result = _run(
        [
            BACKUP_SCRIPT.as_posix(),
            "--dry-run",
            "--backup-dir",
            backup_dir.as_posix(),
            "--retention-days",
            "7",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert backup_dir.as_posix() in result.stdout
    assert "保留天数: 7" in result.stdout
    assert "manifest" in result.stdout
    assert not backup_dir.exists()


def test_backup_rejects_unknown_argument() -> None:
    result = _run([BACKUP_SCRIPT.as_posix(), "--not-a-flag"])
    assert result.returncode != 0
    assert "未知参数" in result.stderr


def test_backup_rejects_invalid_retention() -> None:
    result = _run([BACKUP_SCRIPT.as_posix(), "--dry-run", "--retention-days", "abc"])
    assert result.returncode != 0
    assert "保留天数" in result.stderr


def test_restore_requires_backup_file() -> None:
    result = _run([RESTORE_SCRIPT.as_posix(), "--dry-run"])
    assert result.returncode != 0
    assert "--backup-file" in result.stderr


def test_restore_fails_fast_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sql"
    result = _run([RESTORE_SCRIPT.as_posix(), "--backup-file", missing.as_posix()])
    assert result.returncode != 0
    assert "找不到备份文件" in result.stderr


def test_restore_dry_run_prints_plan(tmp_path: Path) -> None:
    backup_file = tmp_path / "manage_system_20260101_000000.sql"
    backup_file.write_text("-- placeholder\n", encoding="utf-8")
    report_dir = tmp_path / "reports"
    result = _run(
        [
            RESTORE_SCRIPT.as_posix(),
            "--backup-file",
            backup_file.as_posix(),
            "--report-dir",
            report_dir.as_posix(),
            "--max-data-loss-minutes",
            "30",
            "--dry-run",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert backup_file.as_posix() in result.stdout
    assert "可接受丢失量阈值: 30 分钟" in result.stdout
    assert "operation_logs" in result.stdout
    assert not report_dir.exists()
