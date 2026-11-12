"""Plan Your Shot web application.

Run:  uv run uvicorn planyourshot.web.app:app --reload
Jobs: uv run python -m planyourshot.web.worker   (or RUN_WORKER_IN_WEB=1, the SQLite default)
"""
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from planyourshot.web import jobs, monitoring
from planyourshot.web.db import get_db, ping, run_migrations
from planyourshot.web.routes import account, admin, auth, chat, legal, scripts, shots
from planyourshot.web.settings import Settings, get_settings

log = logging.getLogger("planyourshot")


class ConfigError(RuntimeError):
    pass


def production_problems(s: Settings) -> list[str]:
    """Settings that must never reach an https (production) deployment."""
    if not s.https:
        return []
    problems = []
    if not s.session_secret:
        problems.append("SESSION_SECRET is not set")
    if s.auth_dev_login:
        problems.append("AUTH_DEV_LOGIN is enabled")
    if not s.google_configured:
        problems.append("Google OAuth is not configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)")
    return problems


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.auto_migrate:
        run_migrations()
    # Interrupted jobs are recovered by the worker's stale-job sweep (web/jobs.py).
    worker = None
    if s.run_worker_in_web and not s.run_jobs_inline:
        from planyourshot.web.worker import start_thread

        worker = start_thread()
        log.info("running the job worker inside the web process (RUN_WORKER_IN_WEB)")
    try:
        yield
    finally:
        if worker:
            thread, stop = worker
            stop.set()
            jobs.notify()
            thread.join(timeout=5)


def create_app() -> FastAPI:
    s = get_settings()
    problems = production_problems(s)
    if problems:
        raise ConfigError(f"Refusing to start with APP_BASE_URL={s.app_base_url}: " + "; ".join(problems) + ".")
    monitoring.configure_logging()
    monitoring.init_sentry()
    secret = s.session_secret
    if not secret:
        secret = secrets.token_urlsafe(32)
        log.warning("SESSION_SECRET is not set — using a temporary one; sessions reset on restart.")

    app = FastAPI(title="Plan Your Shot", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    # Innermost first: the request log sits inside the session middleware so it can read the user id.
    app.add_middleware(monitoring.RequestLogMiddleware)
    app.add_middleware(
        SessionMiddleware, secret_key=secret, session_cookie="pys_session",
        max_age=60 * 60 * 24 * 30, same_site="lax", https_only=s.https,
    )

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # CSRF defence in depth on top of SameSite=Lax: state-changing API
        # calls must come from our own pages.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in {s.app_base_url, s.frontend_url, str(request.base_url).rstrip("/")}:
                return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
        return await call_next(request)

    app.add_middleware(monitoring.SecurityHeadersMiddleware)  # outermost: every response gets the headers

    for r in (auth.router, account.router, legal.router, admin.router, scripts.router, chat.router, shots.router):
        app.include_router(r)

    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health():
        try:
            ping()
        except Exception:  # noqa: BLE001
            log.exception("health check: database unreachable")
            return JSONResponse({"ok": False, "database": "unreachable"}, status_code=503)
        return {"ok": True, "database": "ok"}

    @app.api_route("/api/health/worker", methods=["GET", "HEAD"])
    def worker_health(db: Session = Depends(get_db)):
        """503 when the oldest ready job has waited longer than JOB_STALE_SECONDS: no worker is taking work."""
        info = jobs.queue_health(db)
        healthy = info["oldest_queued_seconds"] <= get_settings().job_stale_seconds
        return JSONResponse({"ok": healthy, **info}, status_code=200 if healthy else 503)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        raise HTTPException(404, "Not found")

    dist = s.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (dist / path).resolve()
            if path and dist.resolve() in file.parents and file.is_file():
                return FileResponse(file)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
