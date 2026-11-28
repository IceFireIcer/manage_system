from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assistant.service import retention_days, trim_threads  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def cleanup() -> int:
    with SessionLocal() as db:
        days = retention_days(db)
        deleted = trim_threads(db)
        db.commit()
    print(f"[assistant] 留存天数 {days}，清理过期会话 {deleted} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(cleanup())
