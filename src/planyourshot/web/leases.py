"""Database-backed locks that expire.

The web and worker processes share no memory, so the one-model-call-per-user lock
and the global cap on concurrent model calls are rows in `leases`. Every lease has
an expiry: if a process dies while holding one, it simply runs out and can be taken.

Each operation uses its own short transaction, so it never holds the caller's
transaction open (SQLite has a single writer).
"""
import random
import time
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from planyourshot.web.db import new_session
from planyourshot.web.models import Lease, utcnow


def acquire(name: str, ttl_seconds: float) -> str | None:
    """Take the lease `name` if it is free or expired. Returns a token for release(), or None."""
    token = uuid.uuid4().hex
    now = utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    db = new_session()
    try:
        # Take over an expired lease. Only one contender can match the WHERE clause:
        # once it commits, expires_at is in the future again.
        taken = db.execute(
            update(Lease).where(Lease.name == name, Lease.expires_at <= now).values(token=token, expires_at=expires)
        ).rowcount
        if taken:
            db.commit()
            return token
        db.add(Lease(name=name, token=token, expires_at=expires))
        try:
            db.commit()
            return token
        except IntegrityError:  # held by someone else
            db.rollback()
            return None
    finally:
        db.close()


def wait(name: str, ttl_seconds: float, timeout: float | None = None, poll: float = 0.2) -> str | None:
    """acquire(), retrying until `timeout` seconds have passed (None = forever)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        token = acquire(name, ttl_seconds)
        if token or (deadline is not None and time.monotonic() >= deadline):
            return token
        time.sleep(poll if deadline is None else max(0.0, min(poll, deadline - time.monotonic())))


def release(name: str, token: str) -> None:
    db = new_session()
    try:
        db.execute(delete(Lease).where(Lease.name == name, Lease.token == token))
        db.commit()
    finally:
        db.close()


def held(name: str) -> bool:
    db = new_session()
    try:
        return db.scalar(select(func.count()).select_from(Lease).where(
            Lease.name == name, Lease.expires_at > utcnow())) > 0
    finally:
        db.close()


def count(prefix: str) -> int:
    db = new_session()
    try:
        return db.scalar(select(func.count()).select_from(Lease).where(
            Lease.name.startswith(prefix), Lease.expires_at > utcnow())) or 0
    finally:
        db.close()


def purge_expired() -> None:
    db = new_session()
    try:
        db.execute(delete(Lease).where(Lease.expires_at <= utcnow()))
        db.commit()
    finally:
        db.close()


class Semaphore:
    """A counting semaphore made of `size` leases named <prefix>0 … <prefix>N-1."""

    def __init__(self, prefix: str, size: int, ttl_seconds: float):
        self.prefix, self.size, self.ttl = prefix, max(1, size), ttl_seconds

    def try_acquire(self) -> tuple[str, str] | None:
        slots = list(range(self.size))
        random.shuffle(slots)  # spread contention across slots
        for i in slots:
            name = f"{self.prefix}{i}"
            token = acquire(name, self.ttl)
            if token:
                return name, token
        return None

    def acquire(self, timeout: float | None = None, poll: float = 0.2) -> tuple[str, str] | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            handle = self.try_acquire()
            if handle or (deadline is not None and time.monotonic() >= deadline):
                return handle
            time.sleep(poll if deadline is None else max(0.0, min(poll, deadline - time.monotonic())))

    def release(self, handle: tuple[str, str]) -> None:
        release(*handle)

    def in_use(self) -> int:
        return count(self.prefix)
