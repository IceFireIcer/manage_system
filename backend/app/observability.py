from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger("app.ops")

BACKUP_FAILURE_FILE = "backup-failed.log"
MANIFEST_SUFFIX = ".manifest"

_error_times: deque[float] = deque()
_error_lock = threading.Lock()
_error_peak = 0


@dataclass(frozen=True)
class ErrorWindow:
    total: int
    peak: int
    threshold: int
    window_seconds: int
    alert: bool
    alert_message: str | None


def _window_seconds(settings: Settings) -> int:
    return max(int(settings.ops_error_window_seconds), 1)


def _trim(now: float, window_seconds: int) -> None:
    while _error_times and now - _error_times[0] > window_seconds:
        _error_times.popleft()


def reset_errors() -> None:
    global _error_peak
    with _error_lock:
        _error_times.clear()
        _error_peak = 0


def record_error(settings: Settings | None = None, now: float | None = None) -> ErrorWindow:
    global _error_peak
    current = settings or get_settings()
    window_seconds = _window_seconds(current)
    timestamp = time.monotonic() if now is None else now
    with _error_lock:
        _error_times.append(timestamp)
        _trim(timestamp, window_seconds)
        total = len(_error_times)
        if total > _error_peak:
            _error_peak = total
        peak = _error_peak
    threshold = max(int(current.ops_error_alert_threshold), 1)
    alert = total >= threshold
    message = (
        f"请求异常峰值 {total} 次/{window_seconds} 秒，达到阈值 {threshold}"
        if alert
        else None
    )
    if alert:
        logger.warning("运维告警：%s", message)
    return ErrorWindow(
        total=total,
        peak=peak,
        threshold=threshold,
        window_seconds=window_seconds,
        alert=alert,
        alert_message=message,
    )


def error_window(settings: Settings | None = None, now: float | None = None) -> ErrorWindow:
    current = settings or get_settings()
    window_seconds = _window_seconds(current)
    timestamp = time.monotonic() if now is None else now
    with _error_lock:
        _trim(timestamp, window_seconds)
        total = len(_error_times)
        peak = _error_peak
    threshold = max(int(current.ops_error_alert_threshold), 1)
    alert = total >= threshold
    message = (
        f"请求异常峰值 {total} 次/{window_seconds} 秒，达到阈值 {threshold}"
        if alert
        else None
    )
    return ErrorWindow(
        total=total,
        peak=peak,
        threshold=threshold,
        window_seconds=window_seconds,
        alert=alert,
        alert_message=message,
    )


def resolve_backup_dir(settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    configured = current.ops_backup_dir.strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "backups"


def _parse_timestamp(value: str) -> datetime | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _last_line_timestamp(path: Path) -> datetime | None:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return None
    for line in reversed(lines):
        if not line:
            continue
        marker = line.find("失败时间 ")
        if marker < 0:
            continue
        stamp = line[marker + len("失败时间 ") :].split("：")[0].strip()
        parsed = _parse_timestamp(stamp)
        if parsed is not None:
            return parsed
    return None


def _latest_manifest(backup_dir: Path) -> Path | None:
    try:
        manifests = sorted(
            backup_dir.glob(f"*{MANIFEST_SUFFIX}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return manifests[0] if manifests else None


def _manifest_created_at(path: Path) -> datetime | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.startswith("created_at="):
            continue
        return _parse_timestamp(line[len("created_at=") :])
    return None


def backup_observation(
    settings: Settings | None = None, now: datetime | None = None
) -> dict[str, object]:
    current = settings or get_settings()
    timestamp = now or datetime.now(UTC)
    backup_dir = resolve_backup_dir(current)
    max_age = max(int(current.ops_backup_max_age_hours), 1)
    failure_file = backup_dir / BACKUP_FAILURE_FILE

    last_failure = _last_line_timestamp(failure_file) if failure_file.exists() else None
    manifest = _latest_manifest(backup_dir) if backup_dir.exists() else None
    last_backup = _manifest_created_at(manifest) if manifest is not None else None
    age_hours = (
        round((timestamp - last_backup).total_seconds() / 3600, 1)
        if last_backup is not None
        else None
    )

    alerts: list[str] = []
    if last_failure is not None and (timestamp - last_failure).total_seconds() <= max_age * 3600:
        alerts.append(f"数据库备份失败：最近一次失败时间 {last_failure.isoformat()}")
        state = "failed"
    elif last_backup is None:
        state = "absent"
    elif age_hours is not None and age_hours > max_age:
        alerts.append(f"数据库备份已 {age_hours} 小时未更新，超过 {max_age} 小时阈值")
        state = "stale"
    else:
        state = "ok"

    return {
        "dir": str(backup_dir),
        "state": state,
        "last_backup_at": last_backup.isoformat() if last_backup else None,
        "last_failure_at": last_failure.isoformat() if last_failure else None,
        "age_hours": age_hours,
        "max_age_hours": max_age,
        "alert": bool(alerts),
        "alerts": alerts,
    }


def disk_observation(settings: Settings | None = None) -> dict[str, object]:
    current = settings or get_settings()
    backup_dir = resolve_backup_dir(current)
    target = backup_dir if backup_dir.exists() else backup_dir.parent
    min_free = max(int(current.ops_disk_min_free_mb), 0) * 1024 * 1024
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return {
            "path": str(target),
            "free_bytes": None,
            "free_percent": None,
            "min_free_bytes": min_free,
            "alert": False,
            "alerts": [],
        }
    free_percent = round(usage.free / usage.total * 100, 1) if usage.total else 0.0
    alerts = (
        [f"备份目录剩余空间不足：{usage.free} 字节，低于阈值 {min_free} 字节"]
        if usage.free < min_free
        else []
    )
    return {
        "path": str(target),
        "free_bytes": usage.free,
        "free_percent": free_percent,
        "min_free_bytes": min_free,
        "alert": bool(alerts),
        "alerts": alerts,
    }


def collect_observability(settings: Settings | None = None) -> dict[str, object]:
    current = settings or get_settings()
    errors = error_window(current)
    backup = backup_observation(current)
    disk = disk_observation(current)
    alerts: list[str] = []
    if errors.alert and errors.alert_message:
        alerts.append(errors.alert_message)
    alerts.extend(backup["alerts"])
    alerts.extend(disk["alerts"])
    if alerts:
        logger.warning("运维观测告警：%s", "；".join(alerts))
    return {
        "errors": {
            "total": errors.total,
            "peak": errors.peak,
            "threshold": errors.threshold,
            "window_seconds": errors.window_seconds,
            "alert": errors.alert,
        },
        "backup": backup,
        "disk": disk,
        "alerts": alerts,
    }
