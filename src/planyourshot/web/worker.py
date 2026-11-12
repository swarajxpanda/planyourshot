"""The job worker.

    uv run python -m planyourshot.web.worker

Polls the `jobs` table, runs one job at a time, and every few seconds returns stale
jobs (from a worker that died) to the queue. Run as many worker processes as you
like: claims are exclusive. With RUN_WORKER_IN_WEB=1 (the default for SQLite) the
same loop runs in a thread of the web process instead.

On SIGTERM/SIGINT the worker finishes the model call in progress, puts the job back
in the queue at its next checkpoint (without using up an attempt), and exits.
"""
import logging
import os
import signal
import socket
import threading
import time

from planyourshot.web import jobs, leases
from planyourshot.web.settings import get_settings

log = logging.getLogger("planyourshot.worker")

_MAINTENANCE_EVERY = 15.0  # seconds between stale-job sweeps


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def run_once(wid: str = "inline") -> bool:
    """Claim and run one job. Returns False if the queue had nothing ready."""
    claimed = jobs.claim(wid)
    if claimed is None:
        return False
    outcome = jobs.run(claimed)
    log.info("job %s finished: %s", claimed.id, outcome)
    return True


def maintenance() -> None:
    try:
        jobs.requeue_stale()
        leases.purge_expired()
    except Exception:  # noqa: BLE001 - keep the loop alive through a database blip
        log.exception("worker maintenance failed")


def run_forever(stop: threading.Event, wid: str | None = None) -> None:
    wid = wid or worker_id()
    poll = get_settings().job_poll_seconds
    log.info("worker %s started (poll every %ss)", wid, poll)
    last_maintenance = 0.0
    while not stop.is_set():
        if time.monotonic() - last_maintenance > _MAINTENANCE_EVERY:
            maintenance()
            last_maintenance = time.monotonic()
        try:
            if run_once(wid):
                continue
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            stop.wait(poll)
            continue
        jobs.wait_for_work(poll)
    log.info("worker %s stopped", wid)


def start_thread() -> tuple[threading.Thread, threading.Event]:
    """Run the worker loop in a daemon thread (RUN_WORKER_IN_WEB)."""
    stop = threading.Event()
    thread = threading.Thread(target=run_forever, args=(stop, f"web-{worker_id()}"), name="job-worker", daemon=True)
    thread.start()
    return thread, stop


def main() -> None:
    from planyourshot.web import monitoring
    from planyourshot.web.db import run_migrations

    monitoring.configure_logging()
    monitoring.init_sentry()
    if get_settings().auto_migrate:
        run_migrations()  # safe alongside the web process: guarded by a lock on Postgres
    stop = threading.Event()

    def shutdown(signum, _frame):
        log.info("received signal %s; finishing the current step", signum)
        jobs.shutting_down.set()
        stop.set()
        jobs.notify()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    run_forever(stop)


if __name__ == "__main__":
    main()
