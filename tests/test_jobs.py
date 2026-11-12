"""Durable jobs: claiming, retries, stale recovery, resumable generation, cancellation."""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from conftest import upload
from planyourshot.web import jobs, settings, worker
from planyourshot.web.db import new_session
from planyourshot.web.models import Job, Shot, UsageEvent, utcnow


@pytest.fixture
def queued(monkeypatch, env):
    """Jobs stay in the queue until the test runs them (no inline execution)."""
    monkeypatch.setenv("RUN_JOBS_INLINE", "0")
    settings.reset_settings()


def all_jobs():
    db = new_session()
    try:
        return list(db.scalars(select(Job).order_by(Job.id)))
    finally:
        db.close()


def set_job(job_id, **values):
    db = new_session()
    try:
        db.execute(update(Job).where(Job.id == job_id).values(**values))
        db.commit()
    finally:
        db.close()


def shots_of(sid):
    db = new_session()
    try:
        return [(s.id, s.scene_number) for s in db.scalars(select(Shot).where(Shot.script_id == sid).order_by(Shot.id))]
    finally:
        db.close()


def test_upload_only_inserts_a_job_and_the_worker_runs_it(queued, client):
    sid = upload(client).json()["id"]
    assert client.get(f"/api/scripts/{sid}").json()["status"] == "processing"
    [job] = all_jobs()
    assert (job.kind, job.status, job.script_id, job.attempts, job.max_attempts) == ("process_upload", "queued", sid, 0, 3)

    claimed = jobs.claim("w1")
    assert claimed and claimed.id == job.id
    assert jobs.claim("w2") is None  # nothing else ready
    [job] = all_jobs()
    assert (job.status, job.attempts, job.locked_by) == ("running", 1, claimed.token)
    assert jobs.run(claimed) == "done"
    assert client.get(f"/api/scripts/{sid}").json()["status"] == "ready"
    assert all_jobs()[0].status == "done"


def test_concurrent_claims_are_exclusive(queued, client):
    upload(client)
    upload(client, "two.txt")
    got, barrier = [], threading.Barrier(4)

    def grab():
        barrier.wait()
        got.append(jobs.claim("t"))

    threads = [threading.Thread(target=grab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [c.id for c in got if c]
    assert sorted(ids) == sorted(j.id for j in all_jobs()) and len(set(ids)) == 2


def test_a_crash_is_retried_then_fails_with_a_visible_message(queued, client, monkeypatch):
    from planyourshot.web import parsing

    real = parsing.extract_text
    calls = {"n": 0}

    def flaky(path, ext):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        return real(path, ext)

    monkeypatch.setattr(parsing, "extract_text", flaky)
    sid = upload(client).json()["id"]
    assert worker.run_once()
    [job] = all_jobs()
    assert (job.status, job.attempts) == ("queued", 1) and "disk hiccup" in job.error
    assert client.get(f"/api/scripts/{sid}").json()["status"] == "processing"  # still in progress for the UI
    assert worker.run_once()
    assert all_jobs()[0].status == "done"
    assert client.get(f"/api/scripts/{sid}").json()["status"] == "ready"

    monkeypatch.setattr(parsing, "extract_text", lambda *a: (_ for _ in ()).throw(RuntimeError("always")))
    sid2 = upload(client, "two.txt").json()["id"]
    while worker.run_once():
        pass
    job = all_jobs()[-1]
    assert (job.status, job.attempts) == ("failed", 3)
    s = client.get(f"/api/scripts/{sid2}").json()
    assert s["status"] == "failed" and "after several attempts" in s["error"]


def test_retry_waits_for_the_back_off(queued, client, monkeypatch):
    monkeypatch.setenv("JOB_RETRY_DELAY_SECONDS", "60")
    settings.reset_settings()
    from planyourshot.web import parsing

    monkeypatch.setattr(parsing, "extract_text", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    upload(client)
    assert worker.run_once()
    assert all_jobs()[0].available_at > utcnow() + timedelta(seconds=50)
    assert not worker.run_once()  # not ready yet


def test_stale_running_job_is_requeued_and_the_old_worker_is_fenced_off(queued, client, monkeypatch):
    sid = upload(client).json()["id"]
    first = jobs.claim("dead-worker")
    assert jobs.requeue_stale() == 0  # heartbeat is fresh
    set_job(first.id, locked_at=utcnow() - timedelta(seconds=601))
    assert jobs.requeue_stale() == 1
    job = all_jobs()[0]
    assert (job.status, job.locked_by) == ("queued", None) and "put back in the queue" in job.error

    second = jobs.claim("new-worker")
    assert second.id == first.id and all_jobs()[0].attempts == 2
    assert jobs.run(first) == "lost"  # the old worker's token no longer matches
    assert jobs.run(second) == "done"
    assert client.get(f"/api/scripts/{sid}").json()["status"] == "ready"


def test_stale_job_out_of_attempts_fails_visibly(queued, client, monkeypatch):
    monkeypatch.setenv("JOB_STALE_SECONDS", "30")
    settings.reset_settings()
    sid = upload(client).json()["id"]
    for _ in range(3):
        claimed = jobs.claim("w")
        set_job(claimed.id, locked_at=utcnow() - timedelta(seconds=31))
        jobs.requeue_stale()
    job = all_jobs()[0]
    assert (job.status, job.attempts) == ("failed", 3)
    s = client.get(f"/api/scripts/{sid}").json()
    assert s["status"] == "failed" and "after several attempts" in s["error"]


def test_shot_generation_resumes_after_a_crash_without_redoing_finished_scenes(queued, client, ai, monkeypatch):
    sid = upload(client).json()["id"]
    worker.run_once()
    base = f"/api/scripts/{sid}/shots"
    crash = {"armed": True}
    real = ai.scene_shots

    def scene_shots(title, scene_number, *rest):
        if scene_number == 2 and crash.pop("armed", False):
            raise RuntimeError("worker died mid-scene")
        return real(title, scene_number, *rest)

    monkeypatch.setattr(ai, "scene_shots", scene_shots)
    assert client.post(f"{base}/generate", json={}).status_code == 202
    assert client.get(base).json()["status"] == "generating"

    assert worker.run_once()  # scene 1 done, scene 2 crashes
    job = all_jobs()[-1]
    assert (job.status, job.payload["done"], job.progress_done) == ("queued", [1], 1)
    scene1 = [s for s in shots_of(sid) if s[1] == 1]
    assert len(scene1) == 2 and not [s for s in shots_of(sid) if s[1] == 2]
    progress = client.get(base).json()
    assert progress["status"] == "generating" and (progress["done"], progress["total"]) == (1, 2)

    assert worker.run_once()  # the retry
    job = all_jobs()[-1]
    assert job.status == "done" and job.payload["done"] == [1, 2]
    assert [c[1] for c in ai.calls if c[0] == "shots"] == [1, 2]  # scene 1 was not generated again
    after = shots_of(sid)
    assert [s for s in after if s[1] == 1] == scene1  # same rows: not wiped, not duplicated
    assert len([s for s in after if s[1] == 2]) == 2
    data = client.get(base).json()
    assert data["status"] == "ready" and data["error"] == ""

    db = new_session()
    try:
        events = list(db.scalars(select(UsageEvent).order_by(UsageEvent.id)))
    finally:
        db.close()
    full = [e for e in events if e.kind == "shot_generate_full"][0]
    scenes = [e for e in events if e.kind == "shot_generate_scene"]
    assert full.status == "ok" and [e.status for e in scenes] == ["ok", "failed", "ok"]
    assert all(e.run_id == full.id for e in scenes)


def test_deleting_a_script_cancels_its_jobs(queued, client, ai):
    sid = upload(client).json()["id"]
    worker.run_once()
    client.post(f"/api/scripts/{sid}/shots/generate", json={})
    other = upload(client, "two.txt").json()["id"]  # queued upload of another script
    assert [j.status for j in all_jobs()] == ["done", "queued", "queued"]

    # a running job: claimed by a worker, then the screenplay is deleted under it
    running = jobs.claim("w")
    assert client.delete(f"/api/scripts/{sid}").status_code == 204
    statuses = {j.script_id: (j.status, j.error) for j in all_jobs() if j.kind == "generate_shots"}
    assert statuses[sid][0] == "cancelled" and "deleted" in statuses[sid][1]
    assert jobs.run(running) == "lost"
    assert not [c for c in ai.calls if c[0] == "shots"]  # no model calls for a deleted screenplay
    assert shots_of(sid) == []

    assert worker.run_once()  # the other screenplay's job still runs
    assert client.get(f"/api/scripts/{other}").json()["status"] == "ready"


def test_shutdown_puts_the_job_back_without_using_an_attempt(queued, client, ai, monkeypatch):
    sid = upload(client).json()["id"]
    worker.run_once()
    client.post(f"/api/scripts/{sid}/shots/generate", json={})
    ai.before_call = lambda: jobs.shutting_down.set()  # SIGTERM arrives during scene 1
    try:
        assert worker.run_once()
    finally:
        jobs.shutting_down.clear()
        ai.before_call = None
    job = all_jobs()[-1]
    assert (job.status, job.attempts, job.payload["done"]) == ("queued", 0, [1])
    assert worker.run_once() and all_jobs()[-1].status == "done"


def test_worker_thread_in_the_web_process(monkeypatch, env, make_client):
    monkeypatch.setenv("RUN_JOBS_INLINE", "0")
    monkeypatch.setenv("RUN_WORKER_IN_WEB", "1")
    monkeypatch.setenv("JOB_POLL_SECONDS", "0.05")
    settings.reset_settings()
    c = make_client("a@example.com")
    sid = upload(c).json()["id"]
    deadline = time.monotonic() + 10
    while c.get(f"/api/scripts/{sid}").json()["status"] == "processing" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert c.get(f"/api/scripts/{sid}").json()["status"] == "ready"


def test_worker_health_reports_the_oldest_queued_job(queued, client):
    assert client.get("/api/health/worker").json() == {"ok": True, "queued": 0, "running": 0,
                                                       "oldest_queued_seconds": 0}
    upload(client)
    [job] = all_jobs()
    set_job(job.id, created_at=utcnow() - timedelta(seconds=700))
    r = client.get("/api/health/worker")
    assert r.status_code == 503 and r.json()["queued"] == 1 and r.json()["oldest_queued_seconds"] >= 700
