from datetime import date, datetime, timedelta, timezone

SHANGHAI_TZ = timezone(timedelta(hours=8))
REVERSE_RANGE_DETAIL = "开始日期不能晚于结束日期"


def now() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def today() -> date:
    return datetime.now(SHANGHAI_TZ).date()


def normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(SHANGHAI_TZ).replace(tzinfo=None)


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)


def next_day_start(day: date) -> datetime:
    return day_start(day + timedelta(days=1))


def backfill_earliest(days: int, reference: datetime | None = None) -> datetime:
    current = normalize(reference) if reference is not None else now()
    return day_start(current.date() - timedelta(days=days))


def reversed_range(start_date: date | None, end_date: date | None) -> bool:
    return start_date is not None and end_date is not None and start_date > end_date
