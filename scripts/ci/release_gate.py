#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_NOT_DEPLOYABLE = 1
EXIT_USAGE = 2
EXIT_PENDING = 3

EXPECTED_IMAGES = ("labstock-api", "labstock-web")
SHA_TAG_PATTERN = re.compile(r"^sha-([0-9a-f]{7,40})$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCEPTED_EVENTS = ("push", "workflow_dispatch")


class GateError(Exception):
    pass


def _fail(message: str, code: int = EXIT_USAGE) -> int:
    print(f"[release-gate] 错误：{message}", file=sys.stderr)
    return code


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"找不到{label}：{path}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"{label}不是合法 JSON：{exc}") from exc


def _normalize_runs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        runs = payload.get("workflow_runs", [])
    elif isinstance(payload, list):
        runs = payload
    else:
        raise GateError("CI 结论必须是数组或包含 workflow_runs 的对象")
    normalized: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        normalized.append(
            {
                "id": run.get("databaseId", run.get("id")),
                "head_sha": str(run.get("headSha", run.get("head_sha", ""))).lower(),
                "status": str(run.get("status", "")).lower(),
                "conclusion": str(run.get("conclusion") or "").lower(),
                "event": str(run.get("event", "")).lower(),
                "url": run.get("url", run.get("html_url", "")),
                "created_at": str(run.get("createdAt", run.get("created_at", ""))),
            }
        )
    return normalized


def _latest(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not runs:
        return None
    return max(runs, key=lambda run: (run["created_at"], run["id"] or 0))


def evaluate_ci(payload: Any, commit: str) -> dict[str, Any]:
    commit = commit.strip().lower()
    runs = [run for run in _normalize_runs(payload) if run["head_sha"] == commit]
    if not runs:
        return {
            "state": "pending",
            "detail": f"提交 {commit} 尚无 CI 运行记录",
            "run": None,
        }
    preferred = [run for run in runs if run["event"] in ACCEPTED_EVENTS]
    latest = _latest(preferred or runs)
    assert latest is not None
    if latest["status"] != "completed":
        return {
            "state": "pending",
            "detail": f"最新 CI 运行仍在进行（{latest['status']}）",
            "run": latest,
        }
    if latest["conclusion"] != "success":
        return {
            "state": "failed",
            "detail": f"最新 CI 结论为 {latest['conclusion'] or 'unknown'}",
            "run": latest,
        }
    return {"state": "success", "detail": "CI 通过", "run": latest}


def _image_name(name: str) -> str:
    return name.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0].lower()


def evaluate_images(payload: Any, commit: str, tag: str) -> dict[str, Any]:
    commit = commit.strip().lower()
    tag = tag.strip()
    if not isinstance(payload, list):
        raise GateError("镜像清单必须是数组")
    images: dict[str, dict[str, Any]] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = _image_name(str(entry.get("name", "")))
        if name in EXPECTED_IMAGES:
            images[name] = {
                "name": name,
                "tag": str(entry.get("tag", "")),
                "digest": str(entry.get("digest", "")).lower(),
                "revision": str(entry.get("revision", "")).lower(),
            }
    missing = [name for name in EXPECTED_IMAGES if name not in images]
    if missing:
        return {
            "state": "failed",
            "detail": f"缺少镜像：{', '.join(missing)}",
            "images": [],
        }
    errors: list[str] = []
    sha_match = SHA_TAG_PATTERN.match(tag.lower())
    for name in EXPECTED_IMAGES:
        image = images[name]
        if image["tag"].lower() != tag.lower():
            errors.append(f"{name} 标签为 {image['tag']}，期望 {tag}")
        if not DIGEST_PATTERN.match(image["digest"]):
            errors.append(f"{name} digest 缺失或非法")
        if sha_match is not None:
            expected_short = commit[: len(sha_match.group(1))]
            if sha_match.group(1) != expected_short:
                errors.append(
                    f"标签 {image['tag']} 与提交 {commit} 不一致"
                )
        elif not image["revision"].startswith(commit):
            errors.append(f"{name} 未记录与提交 {commit} 一致的 revision")
    if images[EXPECTED_IMAGES[0]]["digest"] == images[EXPECTED_IMAGES[1]]["digest"]:
        errors.append("两个镜像 digest 相同，疑似误用同一镜像")
    if errors:
        return {"state": "failed", "detail": "；".join(errors), "images": []}
    return {
        "state": "success",
        "detail": "两个镜像齐全且与提交绑定",
        "images": [images[name] for name in EXPECTED_IMAGES],
    }


def build_manifest(
    *,
    commit: str,
    tag: str,
    ci: dict[str, Any],
    images: dict[str, Any] | None,
) -> dict[str, Any]:
    deployable = ci["state"] == "success" and (
        images is None or images["state"] == "success"
    )
    manifest: dict[str, Any] = {
        "deployable": deployable,
        "stage": "ci" if images is None else "release",
        "commit": commit,
        "tag": tag,
        "ci": {
            "state": ci["state"],
            "detail": ci["detail"],
            "run_id": (ci["run"] or {}).get("id"),
            "run_url": (ci["run"] or {}).get("url"),
            "conclusion": (ci["run"] or {}).get("conclusion"),
        },
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    if images is not None:
        manifest["images"] = {
            "state": images["state"],
            "detail": images["detail"],
            "items": images["images"],
        }
    return manifest


def _write(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="发布门禁：同一提交的 CI 结论 + 两份镜像齐全才标记可部署",
    )
    parser.add_argument("--commit", required=True, help="完整提交 SHA")
    parser.add_argument("--tag", default="", help="发布门禁使用的镜像标签，如 sha-abc1234")
    parser.add_argument("--ci-summary", required=True, help="CI 运行记录 JSON")
    parser.add_argument("--images", default="", help="镜像 digest JSON；使用 --ci-only 时省略")
    parser.add_argument("--ci-only", action="store_true", help="仅校验 CI 结论")
    parser.add_argument("--output", default="", help="发布记录输出路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    commit = args.commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        return _fail("--commit 必须是 7-40 位十六进制 SHA")
    if not args.ci_only and not args.tag:
        return _fail("校验镜像时必须提供 --tag")

    try:
        ci = evaluate_ci(_load_json(Path(args.ci_summary), " CI 结论"), commit)
        if args.ci_only:
            images = None
        else:
            if not args.images:
                return _fail("校验镜像时必须提供 --images")
            images = evaluate_images(
                _load_json(Path(args.images), "镜像清单"), commit, args.tag
            )
    except GateError as exc:
        return _fail(str(exc))

    manifest = build_manifest(commit=commit, tag=args.tag, ci=ci, images=images)
    if args.output:
        _write(Path(args.output), manifest)

    print(f"[release-gate] 提交 {commit}：{ci['detail']}")
    if images is not None:
        print(f"[release-gate] 镜像：{images['detail']}")
    print(
        f"[release-gate] 结论：{'可部署' if manifest['deployable'] else '不可部署'}"
    )
    if manifest["deployable"]:
        return EXIT_OK
    if ci["state"] == "pending" or (images is not None and images["state"] == "pending"):
        return EXIT_PENDING
    return EXIT_NOT_DEPLOYABLE


if __name__ == "__main__":
    raise SystemExit(main())