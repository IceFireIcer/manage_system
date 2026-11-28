from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import get_settings

DEFAULT_MAX_KEYS = 4096


@dataclass(frozen=True)
class ThrottleLimits:
    account_max_attempts: int
    account_window_seconds: int
    source_max_attempts: int
    source_window_seconds: int
    block_seconds: int


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    logged_block_until: float = 0.0


class LoginThrottle:
    def __init__(
        self,
        limits: Callable[[], ThrottleLimits],
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._max_keys = max(1, max_keys)
        self._accounts: OrderedDict[str, _Bucket] = OrderedDict()
        self._sources: OrderedDict[str, _Bucket] = OrderedDict()

    def reset(self) -> None:
        self._accounts.clear()
        self._sources.clear()

    def check(self, account: str, source: str) -> tuple[int, bool]:
        limits = self._limits()
        if limits.block_seconds <= 0:
            return 0, False
        now = self._clock()
        account_bucket = self._peek(self._accounts, self._account_key(account), now)
        source_bucket = self._peek(self._sources, self._source_key(source), now)
        deadline = 0.0
        if account_bucket is not None and account_bucket.blocked_until > now:
            deadline = account_bucket.blocked_until
        if source_bucket is not None and source_bucket.blocked_until > deadline:
            deadline = source_bucket.blocked_until
        if deadline <= now:
            return 0, False
        retry_after = max(1, math.ceil(deadline - now))
        should_log = self._claim_log(account_bucket, now)
        if not should_log:
            should_log = self._claim_log(source_bucket, now)
        return retry_after, should_log

    def record_failure(self, account: str, source: str) -> bool:
        limits = self._limits()
        if limits.block_seconds <= 0:
            return False
        now = self._clock()
        newly_blocked = False
        if limits.account_max_attempts > 0 and limits.account_window_seconds > 0:
            bucket = self._touch(self._accounts, self._account_key(account), now)
            newly_blocked = (
                self._append_failure(
                    bucket,
                    now,
                    limits.account_window_seconds,
                    limits.account_max_attempts,
                    limits.block_seconds,
                )
                or newly_blocked
            )
        if limits.source_max_attempts > 0 and limits.source_window_seconds > 0:
            bucket = self._touch(self._sources, self._source_key(source), now)
            newly_blocked = (
                self._append_failure(
                    bucket,
                    now,
                    limits.source_window_seconds,
                    limits.source_max_attempts,
                    limits.block_seconds,
                )
                or newly_blocked
            )
        return newly_blocked

    def record_success(self, account: str, source: str) -> None:
        self._accounts.pop(self._account_key(account), None)

    @staticmethod
    def _account_key(account: str) -> str:
        return (account or "").strip().lower() or "<empty>"

    @staticmethod
    def _source_key(source: str) -> str:
        return (source or "").strip() or "<unknown>"

    def _peek(
        self, buckets: OrderedDict[str, _Bucket], key: str, now: float
    ) -> _Bucket | None:
        bucket = buckets.get(key)
        if bucket is None:
            return None
        buckets.move_to_end(key)
        return bucket

    def _touch(
        self, buckets: OrderedDict[str, _Bucket], key: str, now: float
    ) -> _Bucket:
        bucket = buckets.get(key)
        if bucket is not None:
            buckets.move_to_end(key)
            return bucket
        while len(buckets) >= self._max_keys:
            buckets.popitem(last=False)
        bucket = _Bucket()
        buckets[key] = bucket
        return bucket

    @staticmethod
    def _append_failure(
        bucket: _Bucket,
        now: float,
        window_seconds: int,
        max_attempts: int,
        block_seconds: int,
    ) -> bool:
        cutoff = now - window_seconds
        while bucket.failures and bucket.failures[0] < cutoff:
            bucket.failures.popleft()
        bucket.failures.append(now)
        if len(bucket.failures) >= max_attempts and bucket.blocked_until <= now:
            bucket.blocked_until = now + block_seconds
            bucket.logged_block_until = 0.0
            return True
        return False

    @staticmethod
    def _claim_log(bucket: _Bucket | None, now: float) -> bool:
        if bucket is None or bucket.blocked_until <= now:
            return False
        if bucket.logged_block_until < bucket.blocked_until:
            bucket.logged_block_until = bucket.blocked_until
            return True
        return False


def limits_from_settings(settings) -> ThrottleLimits:
    return ThrottleLimits(
        account_max_attempts=max(0, settings.login_throttle_account_max_attempts),
        account_window_seconds=max(0, settings.login_throttle_account_window_seconds),
        source_max_attempts=max(0, settings.login_throttle_source_max_attempts),
        source_window_seconds=max(0, settings.login_throttle_source_window_seconds),
        block_seconds=max(0, settings.login_throttle_block_seconds),
    )



login_throttle = LoginThrottle(lambda: limits_from_settings(get_settings()))
