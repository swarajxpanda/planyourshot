"""Usage metering, plan quotas and model-call concurrency.

Every model call goes through `MeteredAI`, which records one `UsageEvent` per call
(status ok / failed / refused) and holds a slot of the global semaphore while the
call runs. Model endpoints get their `MeteredAI` from the `model_access` dependency,
which first takes the user's single in-flight slot and checks the plan's quotas.

The user's slot and the global semaphore are database leases (web/leases.py), so they
hold across the web and worker processes, and they expire if a process dies.

Quotas count events with status ok or refused; failed calls are free. The
per-minute limit counts every request, failed ones included, because it exists
to protect the model server.
"""
import json
import logging
import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from planyourshot.web import leases, shotfix

from planyourshot.web.ai import AIBusy, AIError, AIService, get_ai
from planyourshot.web.db import get_db, new_session
from planyourshot.web.deps import current_user, ready_script
from planyourshot.web.limits import PLAN_NAMES, Limits, limits_for
from planyourshot.web.models import Script, UsageEvent, User, utcnow
from planyourshot.web.serialize import ts
from planyourshot.web.settings import get_settings

log = logging.getLogger("planyourshot.usage")

KINDS = ("chat", "edit", "shot_chat", "shot_generate_full", "shot_generate_scene", "shot_regenerate")
COUNTED = ("ok", "refused")
BUSY_MESSAGE = "The assistant is still working on your last request."


@dataclass(frozen=True)
class Quota:
    key: str
    kinds: tuple[str, ...]
    window: timedelta
    limit_field: str
    label: str
    window_label: str
    display: str  # the row label in the Usage popover
    standalone_only: bool = False  # ignore scene events that belong to a full run


QUOTAS: dict[str, Quota] = {
    q.key: q for q in (
        Quota("messages", ("chat", "edit", "shot_chat"), timedelta(hours=24), "messages_per_day",
              "assistant messages", "24 hours", "Assistant messages (today)"),
        Quota("full_generations", ("shot_generate_full",), timedelta(days=30), "full_generations_per_month",
              "full shot-list generations", "30 days", "Full shot-list generations (this month)"),
        Quota("scene_generations", ("shot_generate_scene",), timedelta(hours=24), "scene_generations_per_day",
              "single-scene generations", "24 hours", "Scene regenerations (today)", standalone_only=True),
        Quota("shot_regenerations", ("shot_regenerate",), timedelta(hours=24), "shot_regenerations_per_day",
              "shot regenerations", "24 hours", "Shot regenerations (today)"),
    )
}
KIND_QUOTA = {k: q for q in QUOTAS.values() for k in q.kinds}
MINUTE = timedelta(minutes=1)


# ---------- concurrency ----------

def lease_ttl() -> float:
    """How long a lease lives without being released: longer than any one model call
    (a call can be retried once, each attempt up to LLM_TIMEOUT_SECONDS)."""
    return 2 * get_settings().llm_timeout_seconds + 60


class UserSlots:
    """At most one model call in flight per user (a lease named user:<id>)."""

    @staticmethod
    def _name(user_id: str) -> str:
        return f"user:{user_id}"

    def try_acquire(self, user_id: str) -> str | None:
        """Token if the slot was free, else None."""
        return leases.acquire(self._name(user_id), lease_ttl())

    def acquire(self, user_id: str) -> str:
        """Wait for the user's slot (background work, which nobody is waiting on interactively)."""
        return leases.wait(self._name(user_id), lease_ttl())

    def release(self, user_id: str, token: str) -> None:
        leases.release(self._name(user_id), token)

    def busy(self, user_id: str) -> bool:
        return leases.held(self._name(user_id))


user_slots = UserSlots()


def llm_slots() -> leases.Semaphore:
    """Global cap on concurrent model calls (MAX_CONCURRENT_LLM_CALLS), across processes."""
    return leases.Semaphore("llm:", get_settings().max_concurrent_llm_calls, lease_ttl())


def reset_state() -> None:
    """Kept for callers that reset between tests; all state now lives in the database."""


# ---------- metering ----------

def _size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if hasattr(value, "model_dump_json"):
        return len(value.model_dump_json())
    if isinstance(value, (list, tuple)):
        return sum(_size(v) for v in value)
    return len(json.dumps(value, default=str))


def record(user_id: str, script_id: str | None, kind: str, status: str, *, input_chars: int = 0,
           output_chars: int = 0, duration_ms: int = 0, run_id: int | None = None) -> int:
    """Write one usage event in its own session, so it is kept even when the request fails."""
    db = new_session()
    try:
        ev = UsageEvent(user_id=user_id, script_id=script_id, kind=kind, status=status, input_chars=input_chars,
                        output_chars=output_chars, duration_ms=duration_ms, run_id=run_id)
        db.add(ev)
        db.commit()
        return ev.id
    finally:
        db.close()


def finish_run(run_id: int, *, ok: bool, db: Session | None = None) -> None:
    """Close the shot_generate_full event opened when the run was accepted. A run where
    no scene succeeded is marked failed, so it gives the quota back. Sizes and duration
    are the totals of the run's scene events, across every attempt of the job.

    With `db`, the change joins the caller's transaction (the caller commits)."""
    own = db is None
    db = db or new_session()
    try:
        ev = db.get(UsageEvent, run_id)
        if ev is not None:
            ins, outs, ms = db.execute(select(
                func.coalesce(func.sum(UsageEvent.input_chars), 0), func.coalesce(func.sum(UsageEvent.output_chars), 0),
                func.coalesce(func.sum(UsageEvent.duration_ms), 0),
            ).where(UsageEvent.run_id == run_id)).one()
            ev.status = "ok" if ok else "failed"
            ev.input_chars, ev.output_chars, ev.duration_ms = int(ins), int(outs), int(ms)
            if own:
                db.commit()
    finally:
        if own:
            db.close()


class MeteredAI:
    """Wraps an AIService: one usage event and one global slot per call.

    Foreground use (an HTTP request) relies on the `model_access` dependency holding the
    user's slot. Background use (shot-list generation) takes the user's slot per call, so
    the global slot and the user's slot are both free between scenes.
    """

    def __init__(self, ai: AIService, user_id: str, script_id: str | None, *, background: bool = False,
                 run_id: int | None = None):
        self.ai, self.user_id, self.script_id = ai, user_id, script_id
        self.background, self.run_id = background, run_id

    def _run(self, kind: str, call: Callable[[], Any], input_chars: int):
        user_token = user_slots.acquire(self.user_id) if self.background else None
        try:
            slots = llm_slots()
            handle = slots.acquire(None if self.background else get_settings().llm_timeout_seconds)
            if handle is None:
                raise AIBusy("The assistant is busy with other requests right now. Please try again in a minute.")
            started = time.monotonic()
            try:
                out = call()
            except Exception:
                record(self.user_id, self.script_id, kind, "failed", input_chars=input_chars,
                       duration_ms=int((time.monotonic() - started) * 1000), run_id=self.run_id)
                raise
            finally:
                slots.release(handle)
            status = "refused" if getattr(out, "on_topic", True) is False else "ok"
            record(self.user_id, self.script_id, kind, status, input_chars=input_chars, output_chars=_size(out),
                   duration_ms=int((time.monotonic() - started) * 1000), run_id=self.run_id)
            return out
        finally:
            if user_token:
                user_slots.release(self.user_id, user_token)

    # Same signatures as AIService.
    def discuss(self, title, context, history, message):
        return self._run("chat", lambda: self.ai.discuss(title, context, history, message),
                         _size([title, context, message]) + sum(len(c) for _, c in history))

    def propose_edits(self, title, context, history, message):
        return self._run("edit", lambda: self.ai.propose_edits(title, context, history, message),
                         _size([title, context, message]) + sum(len(c) for _, c in history))

    def revise_shots(self, title, context, shots, history, message):
        return self._run("shot_chat", lambda: self.ai.revise_shots(title, context, shots, history, message),
                         _size([title, context, shots, message]) + sum(len(c) for _, c in history))

    def unit(self, kind: str, call: Callable[[], Any], input_chars: int):
        """Meter several model calls as one action: one usage event, one global slot."""
        return self._run(kind, call, input_chars)

    def repairer(self, title: str, scene_text: str):
        """The repair call for incomplete new shots. It runs inside the caller's metered
        unit, so it is part of the same generation for usage and quotas."""
        return lambda incomplete: self.ai.complete_shots(title, scene_text, shotfix.describe(incomplete)).shots

    def regenerate_shot(self, title, scene_text, scene_shots, target, instruction, guidance):
        def call():
            spec = self.ai.regenerate_shot(title, scene_text, scene_shots, target, instruction, guidance)
            shotfix.finish([spec], self.repairer(title, scene_text))
            return spec

        return self._run("shot_regenerate", call, _size([title, scene_text, scene_shots, target, instruction, guidance]))

    def scene_shots(self, title, scene_number, scene_text, previous, guidance):
        def call():
            specs = self.ai.scene_shots(title, scene_number, scene_text, previous, guidance)
            shotfix.finish(specs, self.repairer(title, scene_text))
            return specs

        return self._run("shot_generate_scene", call, _size([title, scene_text, previous, guidance]))


# ---------- quotas ----------

def _since(window: timedelta):
    return utcnow() - window


def _counted(db: Session, user_id: str, quota: Quota):
    q = select(UsageEvent.created_at).where(
        UsageEvent.user_id == user_id, UsageEvent.kind.in_(quota.kinds), UsageEvent.status.in_(COUNTED),
        UsageEvent.created_at > _since(quota.window),
    )
    return q.where(UsageEvent.run_id.is_(None)) if quota.standalone_only else q


def _count(db: Session, query) -> int:
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def _reset_in(db: Session, query, count: int, limit: int, window: timedelta) -> timedelta:
    """Time until enough of the counted events age out of the window to free one."""
    oldest = db.scalar(query.order_by(UsageEvent.created_at).offset(max(0, count - limit)).limit(1))
    return max(timedelta(seconds=1), (oldest + window - utcnow()) if oldest else window)


def _human(delta: timedelta) -> str:
    secs = math.ceil(delta.total_seconds())
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    mins = math.ceil(secs / 60)
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    hours, mins = divmod(mins, 60)
    if hours < 48:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    days = math.ceil(hours / 24)
    return f"{days} days"


def _too_many(message: str, retry_after: timedelta) -> HTTPException:
    return HTTPException(429, message, headers={"Retry-After": str(math.ceil(retry_after.total_seconds()))})


def check_rate(db: Session, user: User, limits: Limits) -> None:
    query = select(UsageEvent.created_at).where(
        UsageEvent.user_id == user.id, UsageEvent.run_id.is_(None), UsageEvent.created_at > _since(MINUTE)
    )
    used, limit = _count(db, query), limits.requests_per_minute
    if used >= limit:
        wait = _reset_in(db, query, used, limit, MINUTE)
        raise _too_many(
            f"Too many requests: the {PLAN_NAMES.get(limits.plan, limits.plan)} plan allows {limit} assistant "
            f"requests per minute. Try again in {_human(wait)}.", wait)


def check_quota(db: Session, user: User, limits: Limits, kind: str) -> None:
    quota = KIND_QUOTA[kind]
    limit = getattr(limits, quota.limit_field)
    plan = PLAN_NAMES.get(limits.plan, limits.plan)
    if limit <= 0:
        raise HTTPException(403, f"The {plan} plan doesn't include {quota.label}.")
    query = _counted(db, user.id, quota)
    used = _count(db, query)
    if used >= limit:
        wait = _reset_in(db, query, used, limit, quota.window)
        raise _too_many(
            f"You've used all {limit} {quota.label} the {plan} plan allows in {quota.window_label}. "
            f"More become available in {_human(wait)}.", wait)


# Plan features listed in the Usage popover: limits field -> label.
FEATURES = {"csv_export": "CSV export"}


def usage_summary(db: Session, user: User) -> dict:
    """Plan, limits and current usage. `rows` and `features` carry everything the UI shows
    about the plan (labels included), so a new plan or a changed limit needs no frontend change.

    For each rolling window, `resets_at` / `reset_in_seconds` say when the oldest counted
    event leaves the window (one unit frees up); both are null when nothing is counted."""
    limits = limits_for(user)
    now = utcnow()
    used: dict[str, int] = {}
    rows: list[dict] = []
    for key, q in QUOTAS.items():
        counted = _counted(db, user.id, q)
        used[key] = _count(db, counted)
        oldest = db.scalar(select(func.min(counted.subquery().c.created_at)))
        resets = oldest + q.window if oldest else None
        rows.append({
            "key": key, "label": q.display, "used": used[key], "limit": getattr(limits, q.limit_field),
            "window": q.window_label, "resets_at": ts(resets),
            "reset_in_seconds": max(0, math.ceil((resets - now).total_seconds())) if resets else None,
        })
    used["requests_last_minute"] = _count(db, select(UsageEvent.id).where(
        UsageEvent.user_id == user.id, UsageEvent.run_id.is_(None), UsageEvent.created_at > _since(MINUTE)))
    used["scripts"] = db.scalar(select(func.count()).select_from(Script).where(Script.user_id == user.id)) or 0
    rows.append({"key": "scripts", "label": "Screenplays", "used": used["scripts"], "limit": limits.max_scripts,
                 "window": None, "resets_at": None, "reset_in_seconds": None})
    return {
        "plan": limits.plan,
        "plan_name": PLAN_NAMES.get(limits.plan, limits.plan),
        "limits": {k: v for k, v in limits.__dict__.items() if k != "plan"},
        "usage": used,
        "rows": rows,
        "features": [{"key": k, "label": label, "included": bool(getattr(limits, k))} for k, label in FEATURES.items()],
    }


# ---------- the dependency ----------

class ModelGuard:
    """What a model endpoint receives once it is admitted: a metered AI service, plus the
    user's in-flight slot, held until the request ends (or `release()` is called)."""

    def __init__(self, user: User, script: Script, ai: AIService, token: str, body: Any = None):
        self.user, self.script, self.body = user, script, body
        self.ai = MeteredAI(ai, user.id, script.id)
        self._token: str | None = token

    def release(self) -> None:
        if self._token:
            token, self._token = self._token, None
            user_slots.release(self.user.id, token)


def admit(db: Session, user: User, script: Script, ai: AIService, kind: str, *, body: Any = None) -> Iterator[ModelGuard]:
    """Take the user's slot, then check limits while holding it (so two requests can't
    both pass a quota with one remaining). Yields the guard; always frees the slot."""
    if kind not in KIND_QUOTA:
        raise ValueError(f"unknown usage kind {kind!r}")
    token = user_slots.try_acquire(user.id)
    if not token:
        raise HTTPException(429, BUSY_MESSAGE, headers={"Retry-After": "5"})
    guard = ModelGuard(user, script, ai, token, body)
    try:
        limits = limits_for(user)
        check_rate(db, user, limits)
        check_quota(db, user, limits, kind)
        yield guard
    finally:
        guard.release()


def model_access(kind: str):
    """Dependency for a model endpoint whose usage kind is fixed (see `admit`)."""

    def dependency(script: Script = Depends(ready_script), user: User = Depends(current_user),
                   db: Session = Depends(get_db), ai: AIService = Depends(get_ai)) -> Iterator[ModelGuard]:
        yield from admit(db, user, script, ai, kind)

    return dependency


def ai_error(exc: AIError) -> HTTPException:
    return HTTPException(getattr(exc, "status", 502), str(exc))
