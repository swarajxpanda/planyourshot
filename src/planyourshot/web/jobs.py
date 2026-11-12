"""Durable background jobs.

The web app only inserts rows into `jobs` (enqueue). The worker (web/worker.py)
claims queued jobs, runs them, and records the outcome:

- Claiming: on Postgres `SELECT ... FOR UPDATE SKIP LOCKED`, so several workers never
  pick the same row; on SQLite a conditional `UPDATE ... WHERE status = 'queued'`.
- A claimed job gets a random token in `locked_by` and a heartbeat in `locked_at`.
  Every progress write checks the token, so a worker whose job was taken away
  (stale, cancelled, or deleted with its screenplay or account) stops at its next
  step instead of overwriting someone else's work.
- A crash raises out of the handler: the job goes back to the queue after
  JOB_RETRY_DELAY_SECONDS × attempts, until max_attempts, then fails with a message
  the user sees on the screenplay.
- A running job whose heartbeat is older than JOB_STALE_SECONDS (the worker died) is
  requeued by `requeue_stale()`, on the same attempt budget.
"""
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from planyourshot.web.db import is_postgres, new_session
from planyourshot.web.models import Job, utcnow
from planyourshot.web.settings import get_settings

log = logging.getLogger("planyourshot.jobs")

KINDS = ("process_upload", "generate_shots")
ACTIVE = ("queued", "running")


class JobLost(Exception):
    """The job is no longer ours: cancelled, deleted, or reclaimed after going stale."""


class Interrupted(Exception):
    """The worker is shutting down; the job goes back to the queue as it was."""


# Set by the worker on SIGTERM; checked at every checkpoint.
shutting_down = threading.Event()


# ---------- enqueueing (web side) ----------

def enqueue(db: Session, kind: str, *, user_id: str, script_id: str | None, payload: dict | None = None,
            total: int = 0, max_attempts: int = 3) -> Job:
    """Add a job to the caller's transaction. Call `notify()` after committing."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    job = Job(kind=kind, user_id=user_id, script_id=script_id, payload=payload or {}, progress_total=total,
              max_attempts=max_attempts)
    db.add(job)
    db.flush()
    return job


_wake = threading.Event()


def notify() -> None:
    """Called after a job was committed. Runs it right away when RUN_JOBS_INLINE is set
    (tests); otherwise wakes an in-process worker thread, if there is one."""
    if get_settings().run_jobs_inline:
        from planyourshot.web import worker

        while worker.run_once():
            pass
    else:
        _wake.set()


def wait_for_work(timeout: float) -> None:
    _wake.wait(timeout)
    _wake.clear()


def cancel_for_script(db: Session, script_id: str) -> int:
    """Cancel the screenplay's queued and running jobs (in the caller's transaction).
    A running worker notices at its next heartbeat and stops."""
    return db.execute(
        update(Job).where(Job.script_id == script_id, Job.status.in_(ACTIVE)).values(
            status="cancelled", error="Cancelled because the screenplay was deleted.", locked_by=None,
            updated_at=utcnow())
    ).rowcount


# ---------- claiming and running (worker side) ----------

@dataclass
class Claim:
    id: int
    token: str


def claim(worker_id: str = "") -> Claim | None:
    """Take the oldest available queued job, or None."""
    token = f"{worker_id[:24]}:{uuid.uuid4().hex}"
    db = new_session()
    try:
        now = utcnow()
        ready = (select(Job.id).where(Job.status == "queued", Job.available_at <= now)
                 .order_by(Job.available_at, Job.id).limit(1))
        if is_postgres(db.get_bind()):
            job_id = db.scalar(ready.with_for_update(skip_locked=True))
            if job_id is None:
                db.rollback()
                return None
            db.execute(update(Job).where(Job.id == job_id).values(
                status="running", attempts=Job.attempts + 1, locked_at=now, locked_by=token, updated_at=now))
            db.commit()
            return Claim(job_id, token)
        # SQLite: one writer at a time, so a conditional UPDATE is atomic. Retry if
        # another thread took the row between the SELECT and the UPDATE.
        for _ in range(5):
            job_id = db.scalar(ready)
            if job_id is None:
                return None
            claimed = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(
                status="running", attempts=Job.attempts + 1, locked_at=now, locked_by=token, updated_at=now)).rowcount
            db.commit()
            if claimed:
                return Claim(job_id, token)
        return None
    finally:
        db.close()


class JobContext:
    """What a handler gets: the job's data, and ways to save progress safely."""

    def __init__(self, db: Session, job: Job, token: str):
        self.db, self.job, self.token = db, job, token

    @property
    def payload(self) -> dict:
        return dict(self.job.payload or {})

    def checkpoint(self, *, payload: dict | None = None, done: int | None = None, total: int | None = None) -> None:
        """Record progress and refresh the heartbeat, as part of the handler's current
        transaction (commit it together with the work it describes). Raises JobLost if
        the job is no longer ours."""
        values: dict = {"locked_at": utcnow(), "updated_at": utcnow()}
        if payload is not None:
            values["payload"] = payload
        if done is not None:
            values["progress_done"] = done
        if total is not None:
            values["progress_total"] = total
        owned = self.db.execute(update(Job).where(
            Job.id == self.job.id, Job.locked_by == self.token, Job.status == "running").values(**values)).rowcount
        if not owned:
            self.db.rollback()
            raise JobLost(self.job.id)
        for k, v in values.items():
            setattr(self.job, k, v)
        if shutting_down.is_set():
            self.db.commit()  # keep the progress just recorded
            raise Interrupted(self.job.id)

    @property
    def last_attempt(self) -> bool:
        return self.job.attempts >= self.job.max_attempts


Handler = Callable[[JobContext], None]
# Called when a job gives up for good, to leave a message where the user sees it.
GiveUp = Callable[[Session, Job, str], None]
_handlers: dict[str, tuple[Handler, GiveUp]] = {}


def handler(kind: str, give_up: GiveUp):
    def register(fn: Handler) -> Handler:
        _handlers[kind] = (fn, give_up)
        return fn
    return register


def _handlers_for(kind: str) -> tuple[Handler, GiveUp]:
    if not _handlers:
        from planyourshot.web import tasks  # noqa: F401  (registers the handlers)
    return _handlers[kind]


def run(claimed: Claim) -> str:
    """Run a claimed job to an outcome. Returns the job's final status as seen here."""
    db = new_session()
    try:
        job = db.get(Job, claimed.id)
        if job is None or job.locked_by != claimed.token or job.status != "running":
            return "lost"
        fn, give_up = _handlers_for(job.kind)
        ctx = JobContext(db, job, claimed.token)
        try:
            fn(ctx)
        except JobLost:
            log.info("job %s was cancelled or reclaimed; stopping", claimed.id)
            return "lost"
        except Interrupted:
            db.rollback()
            back = db.execute(update(Job).where(Job.id == job.id, Job.locked_by == claimed.token).values(
                status="queued", attempts=Job.attempts - 1, locked_by=None, locked_at=None, available_at=utcnow(),
                updated_at=utcnow())).rowcount
            db.commit()
            return "queued" if back else "lost"
        except Exception as exc:  # noqa: BLE001 - a crash in a handler must not kill the worker
            db.rollback()
            log.exception("job %s (%s) failed on attempt %s", job.id, job.kind, job.attempts)
            return _retry_or_fail(db, claimed, give_up, f"{type(exc).__name__}: {exc}"[:500])
        done = db.execute(update(Job).where(Job.id == job.id, Job.locked_by == claimed.token, Job.status == "running")
                          .values(status="done", locked_by=None, locked_at=None, error="", updated_at=utcnow())).rowcount
        db.commit()
        return "done" if done else "lost"
    finally:
        db.close()


GAVE_UP = {
    "process_upload": "Processing failed after several attempts. Use Retry to try again.",
    "generate_shots": "Shot generation stopped after several attempts. Generate again to finish.",
}


def _retry_or_fail(db: Session, claimed: Claim, give_up: GiveUp, error: str) -> str:
    job = db.get(Job, claimed.id)
    if job is None or job.locked_by != claimed.token or job.status != "running":
        db.rollback()
        return "lost"
    now = utcnow()
    if job.attempts < job.max_attempts:
        delay = get_settings().job_retry_delay_seconds * job.attempts
        job.status, job.locked_by, job.locked_at = "queued", None, None
        job.available_at, job.error, job.updated_at = now + timedelta(seconds=delay), error, now
        db.commit()
        return "queued"
    job.status, job.locked_by, job.locked_at, job.error, job.updated_at = "failed", None, None, error, now
    give_up(db, job, GAVE_UP.get(job.kind, "This job failed."))
    db.commit()
    return "failed"


def requeue_stale() -> int:
    """Return running jobs whose heartbeat is older than JOB_STALE_SECONDS to the queue
    (their worker is gone). A job out of attempts fails instead. Returns how many."""
    cutoff = utcnow() - timedelta(seconds=get_settings().job_stale_seconds)
    db = new_session()
    try:
        stale = list(db.scalars(select(Job).where(Job.status == "running", Job.locked_at < cutoff)))
        for job in stale:
            log.warning("job %s (%s) went stale after attempt %s", job.id, job.kind, job.attempts)
            job.locked_by, job.locked_at, job.updated_at = None, None, utcnow()
            if job.attempts < job.max_attempts:
                job.status, job.available_at = "queued", utcnow()
                job.error = "The worker stopped while running this job; it was put back in the queue."
            else:
                job.status, job.error = "failed", "The worker stopped while running this job too many times."
                _handlers_for(job.kind)[1](db, job, GAVE_UP.get(job.kind, "This job failed."))
        db.commit()
        return len(stale)
    finally:
        db.close()


def queue_health(db: Session) -> dict:
    now = utcnow()
    oldest = db.scalar(select(func.min(Job.created_at)).where(Job.status == "queued", Job.available_at <= now))
    counts = dict(db.execute(select(Job.status, func.count()).where(Job.status.in_(ACTIVE)).group_by(Job.status)).all())
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "oldest_queued_seconds": round((now - oldest).total_seconds(), 1) if oldest else 0,
    }
