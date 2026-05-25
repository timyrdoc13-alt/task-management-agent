"""In-process rate limits (PERMISSIONS.md buckets)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from kaiten_api import ENV


@dataclass
class RateLimitError(Exception):
    bucket: str
    retry_after_s: float

    def __str__(self) -> str:
        return f"rate_limited:{self.bucket}:retry_after={self.retry_after_s:.0f}s"


class RateLimiter:
    """Sliding-window limits per bucket."""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self.kaiten_writes_this_run = 0
        self.web_fetches_this_run = 0
        self.artifacts_this_run = 0

    def _limit(self, bucket: str) -> tuple[int, int]:
        if bucket == "kaiten_read":
            return int(ENV.get("RATE_KAITEN_READS_PER_MIN", "60")), 60
        if bucket == "kaiten_write":
            return int(ENV.get("RATE_KAITEN_WRITES_PER_RUN", "10")), 0
        if bucket == "web_fetch":
            return int(ENV.get("RATE_WEB_FETCHES_PER_RUN", "10")), 0
        if bucket == "reminder":
            return int(ENV.get("RATE_REMINDERS_PER_HOUR", "5")), 3600
        if bucket == "artifact":
            return int(ENV.get("RATE_ARTIFACTS_PER_RUN", "3")), 0
        return 999, 60

    def check(self, bucket: str) -> None:
        limit, window_s = self._limit(bucket)
        if bucket == "kaiten_write":
            if self.kaiten_writes_this_run >= limit:
                raise RateLimitError(bucket, 0)
            return
        if bucket == "web_fetch":
            if self.web_fetches_this_run >= limit:
                raise RateLimitError(bucket, 0)
            return
        if bucket == "artifact":
            if self.artifacts_this_run >= limit:
                raise RateLimitError(bucket, 0)
            return
        if window_s <= 0:
            return
        now = time.time()
        q = self._windows[bucket]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            retry = window_s - (now - q[0]) if q else window_s
            raise RateLimitError(bucket, max(retry, 1.0))
        q.append(now)

    def record(self, bucket: str) -> None:
        if bucket == "kaiten_write":
            self.kaiten_writes_this_run += 1
        elif bucket == "web_fetch":
            self.web_fetches_this_run += 1
        elif bucket == "artifact":
            self.artifacts_this_run += 1
        else:
            self.check(bucket)
