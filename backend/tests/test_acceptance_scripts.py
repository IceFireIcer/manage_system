from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONCURRENCY_SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "run-mysql-concurrency.sh"
DEMO_COMPOSE = REPO_ROOT / "docker-compose.demo.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="需要 bash 才能校验验收脚本")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_concurrency_script_exists_and_passes_syntax_check() -> None:
    assert CONCURRENCY_SCRIPT.is_file()
    assert CONCURRENCY_SCRIPT.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/env bash"
    result = _run(["-n", CONCURRENCY_SCRIPT.as_posix()])
    assert result.returncode == 0, result.stderr


def test_concurrency_script_dry_run_prints_isolated_plan() -> None:
    result = _run(
        [CONCURRENCY_SCRIPT.as_posix(), "--dry-run", "--port", "13307"]
    )

    assert result.returncode == 0, result.stderr
    assert "manage_system_concurrency" in result.stdout
    assert "13307" in result.stdout
    assert "test_mysql_concurrency.py" in result.stdout


def test_concurrency_script_rejects_invalid_port() -> None:
    result = _run([CONCURRENCY_SCRIPT.as_posix(), "--port", "abc"])

    assert result.returncode != 0
    assert "端口必须为数字" in result.stderr


def test_concurrency_test_skips_without_mysql_url() -> None:
    source = (REPO_ROOT / "backend" / "tests" / "test_mysql_concurrency.py").read_text(
        encoding="utf-8"
    )

    assert "LABSTOCK_MYSQL_TEST_URL" in source
    assert "pytest.mark.skipif" in source
    assert "真实 MySQL" in source


def test_demo_compose_is_isolated_from_production() -> None:
    content = DEMO_COMPOSE.read_text(encoding="utf-8")

    assert "name: labstock-demo" in content
    assert "MYSQL_DATABASE: manage_system_demo" in content
    assert "demo_mysql_data" in content
    assert "APP_ENV: demo" in content
    assert "manage_system_demo?charset=utf8mb4" in content
    assert "build:" not in content

    production = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "manage_system_demo" not in production
    assert "demo_mysql_data" not in production


def test_publish_workflow_gates_on_same_commit_tests_and_both_images() -> None:
    content = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "actions: read" in content
    assert "needs: gate" in content
    assert "release_gate.py" in content
    assert "release-manifest.json" in content
    assert "labstock-api" in content
    assert "labstock-web" in content
    assert "sha-${GITHUB_SHA::7}" in content
