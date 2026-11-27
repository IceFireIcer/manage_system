from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "ci" / "release_gate.py"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TAG = "sha-0123456"


@pytest.fixture()
def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_gate", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _ci(payload, tmp_path: Path, name: str = "ci.json") -> str:
    return _write(tmp_path / name, payload)


def _success_ci(commit: str = COMMIT, event: str = "push") -> dict:
    return {
        "workflow_runs": [
            {
                "id": 42,
                "head_sha": commit,
                "status": "completed",
                "conclusion": "success",
                "event": event,
                "html_url": "https://example.invalid/run/42",
                "created_at": "2026-11-27T10:00:00Z",
            }
        ]
    }


def _images(tag: str = TAG, commit: str = COMMIT) -> list[dict]:
    return [
        {
            "name": f"ghcr.io/icefireicer/labstock-api:{tag}",
            "tag": tag,
            "digest": "sha256:" + "1" * 64,
            "revision": commit,
        },
        {
            "name": f"ghcr.io/icefireicer/labstock-web:{tag}",
            "tag": tag,
            "digest": "sha256:" + "2" * 64,
            "revision": commit,
        },
    ]


def test_gate_marks_deployable_with_ci_and_two_images(gate, tmp_path, capsys):
    output = tmp_path / "release.json"
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            TAG,
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", _images()),
            "--output",
            str(output),
        ]
    )

    assert result == gate.EXIT_OK
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["deployable"] is True
    assert manifest["stage"] == "release"
    assert manifest["ci"]["run_id"] == 42
    assert [item["name"] for item in manifest["images"]["items"]] == [
        "labstock-api",
        "labstock-web",
    ]
    assert "结论：可部署" in capsys.readouterr().out


def test_ci_only_does_not_require_images(gate, tmp_path):
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--ci-only",
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
        ]
    )

    assert result == gate.EXIT_OK


def test_ci_failure_blocks_release(gate, tmp_path):
    payload = _success_ci()
    payload["workflow_runs"][0]["conclusion"] = "failure"
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--ci-only",
            "--ci-summary",
            _ci(payload, tmp_path),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_ci_in_progress_is_pending(gate, tmp_path):
    payload = _success_ci()
    payload["workflow_runs"][0]["status"] = "in_progress"
    payload["workflow_runs"][0]["conclusion"] = ""
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--ci-only",
            "--ci-summary",
            _ci(payload, tmp_path),
        ]
    )

    assert result == gate.EXIT_PENDING


def test_missing_commit_ci_is_pending(gate, tmp_path):
    payload = _success_ci(commit="f" * 40)
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--ci-only",
            "--ci-summary",
            _ci(payload, tmp_path),
        ]
    )

    assert result == gate.EXIT_PENDING


def test_missing_web_image_blocks_release(gate, tmp_path):
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            TAG,
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", _images()[:1]),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_tag_not_matching_commit_blocks_release(gate, tmp_path):
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            "sha-fffffff",
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", _images(tag="sha-fffffff")),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_same_digest_for_both_images_blocks_release(gate, tmp_path):
    images = _images()
    images[1]["digest"] = images[0]["digest"]
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            TAG,
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", images),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_invalid_digest_blocks_release(gate, tmp_path):
    images = _images()
    images[0]["digest"] = "sha256:not-a-digest"
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            TAG,
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", images),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_non_sha_tag_requires_revision(gate, tmp_path):
    images = _images(tag="v20261127-1")
    images[0]["revision"] = "f" * 40
    result = gate.main(
        [
            "--commit",
            COMMIT,
            "--tag",
            "v20261127-1",
            "--ci-summary",
            _ci(_success_ci(), tmp_path),
            "--images",
            _write(tmp_path / "images.json", images),
        ]
    )

    assert result == gate.EXIT_NOT_DEPLOYABLE


def test_usage_errors(gate, tmp_path):
    ci = _ci(_success_ci(), tmp_path)
    assert (
        gate.main(["--commit", "not-a-sha", "--ci-only", "--ci-summary", ci])
        == gate.EXIT_USAGE
    )
    assert (
        gate.main(
            [
                "--commit",
                COMMIT,
                "--ci-only",
                "--ci-summary",
                str(tmp_path / "missing.json"),
            ]
        )
        == gate.EXIT_USAGE
    )
    assert (
        gate.main(
            [
                "--commit",
                COMMIT,
                "--ci-summary",
                ci,
                "--images",
                _write(tmp_path / "images.json", _images()),
            ]
        )
        == gate.EXIT_USAGE
    )
