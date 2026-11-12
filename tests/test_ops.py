"""Admin access, legal pages, startup refusal, security headers, health, leases,
Sentry scrubbing, request logs and database URLs."""
import logging
import time
from datetime import timedelta

import pytest
from sqlalchemy import update

from conftest import set_user, upload
from planyourshot.web import leases, monitoring, settings, usage
from planyourshot.web.db import new_session
from planyourshot.web.models import Lease, utcnow
from planyourshot.web.ratelimit import DatabaseRateLimiter
from planyourshot.web.settings import normalize_database_url


# ---------- admin ----------

@pytest.fixture
def admins(monkeypatch, env):
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.com, ops@example.com")
    settings.reset_settings()


ADMIN_ROUTES = [("get", "/api/admin/overview"), ("get", "/api/admin/users?q=a")]


def test_admin_is_404_for_everyone_else(admins, make_client):
    anon = make_client()
    user = make_client("a@example.com")
    target = user.get("/api/me").json()["id"]
    for c in (anon, user):
        for method, path in ADMIN_ROUTES:
            assert getattr(c, method)(path).status_code == 404, path
        r = c.post(f"/api/admin/users/{target}/disabled", json={"disabled": True})
        assert r.status_code == 404
    assert user.get("/api/me").json()["is_admin"] is False


def test_admin_overview_and_disabling_a_user(admins, make_client, ai):
    user = make_client("a@example.com")
    sid = upload(user).json()["id"]
    user.post(f"/api/scripts/{sid}/chat", json={"message": "Who is Sarah?"})
    user.post(f"/api/scripts/{sid}/chat", json={"message": "what is 5+5"})
    ai.fail = True
    user.post(f"/api/scripts/{sid}/chat", json={"message": "again"})
    ai.fail = False

    boss = make_client("boss@example.com")  # case-insensitive match
    assert boss.get("/api/me").json()["is_admin"] is True
    data = boss.get("/api/admin/overview").json()
    assert data["days"] == [{"day": utcnow().date().isoformat(), "calls": 3, "failed": 1, "refused": 1}]
    assert data["queue"]["queued"] == 0 and data["queue"]["failed_total"] == 0
    top = data["top_users"][0]
    assert top["email"] == "a@example.com" and top["calls"] == 3 and not top["disabled"]
    assert data["totals"] == {"users": 2, "scripts": 1}
    assert [u["email"] for u in boss.get("/api/admin/users?q=A@EX").json()["users"]] == ["a@example.com"]

    r = boss.post(f"/api/admin/users/{top['id']}/disabled", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert user.get("/api/scripts").status_code == 403
    boss.post(f"/api/admin/users/{top['id']}/disabled", json={"disabled": False})
    assert user.get("/api/scripts").status_code == 200

    me = boss.get("/api/me").json()["id"]
    assert boss.post(f"/api/admin/users/{me}/disabled", json={"disabled": True}).status_code == 400


def test_disabled_admin_loses_access(admins, make_client):
    boss = make_client("boss@example.com")
    set_user("boss@example.com", disabled=True)
    assert boss.get("/api/admin/overview").status_code == 404


# ---------- legal pages ----------

def test_legal_pages_are_public(make_client):
    anon = make_client()
    for name, title in (("privacy", "Privacy Policy"), ("terms", "Terms of Service")):
        r = anon.get(f"/api/legal/{name}")
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == title and body["markdown"].startswith(f"# {title}")
    assert anon.get("/api/legal/cookies").status_code == 404
    assert anon.get("/api/legal/..%2F.env").status_code == 404


# ---------- startup refusal ----------

def _prod(monkeypatch, **env):
    base = {"APP_BASE_URL": "https://planyourshot.example", "SESSION_SECRET": "s" * 40,
            "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret", "AUTH_DEV_LOGIN": "0"}
    for k, v in {**base, **env}.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    settings.reset_settings()


@pytest.mark.parametrize("env_change, problem", [
    ({"SESSION_SECRET": None}, "SESSION_SECRET is not set"),
    ({"AUTH_DEV_LOGIN": "1"}, "AUTH_DEV_LOGIN is enabled"),
    ({"GOOGLE_CLIENT_SECRET": None}, "Google OAuth is not configured"),
    ({"GOOGLE_CLIENT_ID": ""}, "Google OAuth is not configured"),
])
def test_https_deployment_refuses_unsafe_settings(env, monkeypatch, env_change, problem):
    from planyourshot.web.app import ConfigError, create_app

    _prod(monkeypatch, **env_change)
    with pytest.raises(ConfigError, match=problem):
        create_app()


def test_https_deployment_lists_every_problem_and_starts_when_fixed(env, monkeypatch):
    from planyourshot.web.app import ConfigError, create_app

    _prod(monkeypatch, SESSION_SECRET=None, AUTH_DEV_LOGIN="1", GOOGLE_CLIENT_ID=None)
    with pytest.raises(ConfigError) as err:
        create_app()
    assert all(p in str(err.value) for p in ("SESSION_SECRET", "AUTH_DEV_LOGIN", "Google OAuth"))
    _prod(monkeypatch)
    assert create_app() is not None
    _prod(monkeypatch, APP_BASE_URL="http://localhost:8000", SESSION_SECRET=None, AUTH_DEV_LOGIN="1",
          GOOGLE_CLIENT_ID=None)
    assert create_app() is not None  # local http development is unaffected


# ---------- security headers and health ----------

def test_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "database": "ok"}
    h = r.headers
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    assert "https://fonts.googleapis.com" in csp and "https://fonts.gstatic.com" in csp
    assert "https://*.googleusercontent.com" in csp and "unsafe-inline" not in csp
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-frame-options"] == "DENY" and "strict-transport-security" not in h  # http in tests
    assert "content-security-policy" not in client.get("/api/docs").headers  # Swagger UI needs its CDN
    assert client.get("/api/nope").headers["x-content-type-options"] == "nosniff"  # errors too


def test_csp_allows_sentry_only_when_configured(env, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://abc@o1.ingest.sentry.io/2")
    monkeypatch.setenv("VITE_SENTRY_DSN", "https://def@o9.ingest.de.sentry.io/3")
    settings.reset_settings()
    csp = monitoring.content_security_policy()
    assert "connect-src 'self' https://o1.ingest.sentry.io https://o9.ingest.de.sentry.io" in csp
    monkeypatch.delenv("SENTRY_DSN")
    monkeypatch.delenv("VITE_SENTRY_DSN")
    settings.reset_settings()
    assert "connect-src 'self';" in monitoring.content_security_policy()


def test_health_is_503_when_the_database_is_down(client, monkeypatch):
    import planyourshot.web.app as app_module

    def down():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(app_module, "ping", down)
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json() == {"ok": False, "database": "unreachable"}


# ---------- database-backed leases and rate limits ----------

def test_leases_are_exclusive_and_expire(migrated):
    a = leases.acquire("user:x", ttl_seconds=0.3)
    assert a and leases.acquire("user:x", ttl_seconds=0.3) is None and leases.held("user:x")
    time.sleep(0.35)
    assert not leases.held("user:x")
    b = leases.acquire("user:x", ttl_seconds=30)  # a crashed holder's lease ran out: take it
    assert b and b != a
    leases.release("user:x", a)  # the old holder's late release doesn't free the new lease
    assert leases.held("user:x")
    leases.release("user:x", b)
    assert not leases.held("user:x")


def test_a_crashed_process_does_not_lock_a_user_out(client, script):
    me = client.get("/api/me").json()["id"]
    assert usage.user_slots.try_acquire(me)  # held by a process that then died
    r = client.post(f"/api/scripts/{script['id']}/chat", json={"message": "Who is Sarah?"})
    assert r.status_code == 429
    db = new_session()
    try:  # fast-forward past the lease's expiry
        db.execute(update(Lease).where(Lease.name == f"user:{me}").values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    finally:
        db.close()
    assert client.post(f"/api/scripts/{script['id']}/chat", json={"message": "Who is Sarah?"}).status_code == 200


def test_semaphore_caps_holders_across_instances(migrated):
    one, two = leases.Semaphore("llm:", 2, 30), leases.Semaphore("llm:", 2, 30)  # e.g. web and worker
    h1, h2 = one.try_acquire(), two.try_acquire()
    assert h1 and h2 and one.try_acquire() is None and two.in_use() == 2
    assert two.acquire(timeout=0.1) is None
    one.release(h1)
    assert two.try_acquire() is not None


def test_database_rate_limiter_is_shared(migrated):
    web, worker = DatabaseRateLimiter(), DatabaseRateLimiter()
    assert web.hit("k", 2, 60) is None and worker.hit("k", 2, 60) is None
    wait = web.hit("k", 2, 60)
    assert wait is not None and 55 < wait <= 60
    assert worker.hit("other", 2, 60) is None
    db = new_session()
    try:  # age the hits out of the window
        from planyourshot.web.models import RateLimitHit

        db.execute(update(RateLimitHit).values(created_at=utcnow() - timedelta(seconds=61)))
        db.commit()
    finally:
        db.close()
    assert web.hit("k", 2, 60) is None


# ---------- monitoring ----------

def test_sentry_events_are_scrubbed():
    event = {
        "request": {"url": "https://x/api/scripts/1/chat", "method": "POST", "data": {"message": "SECRET LINE"},
                    "cookies": {"pys_session": "abc"}, "query_string": "q=1",
                    "headers": {"Cookie": "pys_session=abc", "User-Agent": "UA", "Authorization": "x"}},
        "user": {"id": "u1", "email": "a@example.com", "ip_address": "1.2.3.4"},
        "exception": {"values": [{"stacktrace": {"frames": [{"function": "chat", "vars": {"text": "SECRET LINE"}}]}}]},
        "extra": {"body": "SECRET LINE"},
        "breadcrumbs": {"values": [
            {"category": "console", "message": "SECRET LINE"},
            {"category": "httplib", "data": {"url": "http://ollama/api/chat", "method": "POST", "body": "SECRET"}},
        ]},
    }
    out = monitoring.scrub_event(event)
    flat = repr(out)
    assert "SECRET" not in flat and "a@example.com" not in flat and "pys_session" not in flat and "1.2.3.4" not in flat
    assert out["user"] == {"id": "u1"} and out["request"]["headers"] == {"User-Agent": "UA"}
    assert out["breadcrumbs"]["values"] == [{"category": "httplib",
                                             "data": {"url": "http://ollama/api/chat", "method": "POST"}}]


def test_sentry_init_only_with_a_dsn(env, monkeypatch):
    assert monitoring.init_sentry() is False
    seen = {}
    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setenv("SENTRY_DSN", "https://abc@o1.ingest.sentry.io/2")
    settings.reset_settings()
    assert monitoring.init_sentry() is True
    assert seen["send_default_pii"] is False and seen["max_request_body_size"] == "never"
    assert seen["include_local_variables"] is False and seen["before_send"] is monitoring.scrub_event


def test_request_log_has_the_fields_and_never_the_body(client, script):
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Grab()
    monitoring.request_log.addHandler(handler)
    try:
        client.post(f"/api/scripts/{script['id']}/chat", json={"message": "A VERY PRIVATE LINE"})
    finally:
        monitoring.request_log.removeHandler(handler)
    [rec] = [r for r in records if r.fields["path"].endswith("/chat")]
    me = client.get("/api/me").json()["id"]
    assert rec.fields["method"] == "POST" and rec.fields["status"] == 200 and rec.fields["user_id"] == me
    assert rec.fields["duration_ms"] >= 0
    line = monitoring.JsonFormatter().format(rec)
    assert "PRIVATE" not in line and '"user_id": "' + me in line


# ---------- database URLs ----------

@pytest.mark.parametrize("url, expected", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///data/app.db", "sqlite:///data/app.db"),
])
def test_database_url_forms(url, expected):
    assert normalize_database_url(url) == expected
