"""Rate limits for routes that don't call the model (sign-in, uploads).

`RateLimiter` is the interface. `DatabaseRateLimiter` is the one in use: the web and
worker processes (and several web processes) share it through the database.
`InMemoryRateLimiter` is kept for single-process use and tests. A Redis
implementation of `hit()` can be dropped in through `get_limiter()`.
"""
import math
import random
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Protocol

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select

from planyourshot.web.db import new_session
from planyourshot.web.deps import current_user
from planyourshot.web.models import RateLimitHit, User, utcnow
from planyourshot.web.settings import get_settings

# Hits older than this are never needed again (the longest window is an hour).
_KEEP = timedelta(hours=2)


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: float) -> float | None:
        """Count one request for `key`. Returns None if allowed, otherwise the
        number of seconds until the next request would be allowed."""


class InMemoryRateLimiter:
    """Sliding-window log per key."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def hit(self, key: str, limit: int, window_seconds: float) -> float | None:
        now = self._clock()
        with self._lock:
            log = self._hits.setdefault(key, deque())
            while log and log[0] <= now - window_seconds:
                log.popleft()
            if len(log) >= limit:
                return max(0.0, log[0] + window_seconds - now)
            log.append(now)
            self._calls += 1
            if self._calls % 1000 == 0:  # keep memory bounded: drop idle keys
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 3600}
            return None


class DatabaseRateLimiter:
    """Sliding-window log in the `rate_limit_hits` table.

    The hit is written first and then counted, and is taken back if it is over the
    limit. Two simultaneous requests can therefore both be refused near the limit,
    but never both admitted past it."""

    def hit(self, key: str, limit: int, window_seconds: float) -> float | None:
        now = utcnow()
        since = now - timedelta(seconds=window_seconds)
        db = new_session()
        try:
            row = RateLimitHit(key=key, created_at=now)
            db.add(row)
            db.commit()
            in_window = select(RateLimitHit).where(RateLimitHit.key == key, RateLimitHit.created_at > since)
            used = db.scalar(select(func.count()).select_from(in_window.subquery())) or 0
            if used <= limit:
                if random.random() < 0.01:  # occasional clean-up of old rows
                    db.execute(delete(RateLimitHit).where(RateLimitHit.created_at < now - _KEEP))
                    db.commit()
                return None
            db.delete(row)
            db.commit()
            # The request frees up when the hit `limit` places back ages out.
            oldest = db.scalar(select(RateLimitHit.created_at).where(
                RateLimitHit.key == key, RateLimitHit.created_at > since,
            ).order_by(RateLimitHit.created_at.desc()).offset(limit - 1).limit(1)) if limit > 0 else None
            if oldest is None:
                return window_seconds
            return max(0.0, (oldest + timedelta(seconds=window_seconds) - now).total_seconds())
        finally:
            db.close()

    def forget(self, prefix: str) -> None:
        db = new_session()
        try:
            db.execute(delete(RateLimitHit).where(RateLimitHit.key.startswith(prefix)))
            db.commit()
        finally:
            db.close()


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = DatabaseRateLimiter()
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None


def enforce(key: str, limit: int, window_seconds: float, message: str) -> None:
    wait = get_limiter().hit(key, limit, window_seconds)
    if wait is not None:
        secs = max(1, math.ceil(wait))
        mins = math.ceil(secs / 60)
        text = f"{secs} second{'s' if secs != 1 else ''}" if secs < 60 else f"{mins} minute{'s' if mins != 1 else ''}"
        raise HTTPException(429, message.format(wait=text), headers={"Retry-After": str(secs)})


def client_ip(request: Request) -> str:
    # request.client is the direct peer. Behind a reverse proxy, run uvicorn with
    # --proxy-headers (and --forwarded-allow-ips) so this is the real client address.
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    """Per-IP limit on /api/auth/*."""
    enforce(f"auth:{client_ip(request)}", get_settings().auth_rate_limit_per_minute, 60,
            "Too many sign-in requests from this address. Try again in {wait}.")


def upload_rate_limit(user: User = Depends(current_user)) -> None:
    """Per-user limit on uploads, counted per attempt."""
    enforce(f"upload:{user.id}", get_settings().upload_rate_limit_per_hour, 3600,
            "Too many uploads in the last hour. Try again in {wait}.")
