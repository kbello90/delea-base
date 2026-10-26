"""In-process request limits for the public storefront endpoints.

A sliding window per key (the client address). Each API instance counts on its own, which is
enough for cheap reads; writes that must hold across instances (checkout, password reset) count
in Postgres instead.

The client address is the one uvicorn reports. Behind a proxy that depends on
FORWARDED_ALLOW_IPS, as for the password-reset limit (TRD §6.10): it must be set on the
deployed service before the storefront goes live.
"""

import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Count one request for `key`. None if allowed, else the seconds until it would be."""
        now = time.monotonic()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._prune(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(hits[0] + self.window - now, 0.0)
        hits.append(now)
        return None

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
        if len(self._hits) >= self.max_keys:
            self._hits.clear()

    def reset(self) -> None:
        self._hits.clear()


# Every public storefront request (reads and CORS preflights), per client address.
public_reads = SlidingWindowLimiter(limit=120, window_seconds=60)
# On top of that: cart quotes and order tracking, per client address. Orders are limited in
# Postgres (storefront_count_attempt, storefront_place_order), so they hold across instances.
public_quotes = SlidingWindowLimiter(limit=60, window_seconds=60)
public_tracking = SlidingWindowLimiter(limit=30, window_seconds=60)
