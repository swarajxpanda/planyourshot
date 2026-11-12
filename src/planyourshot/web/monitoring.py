"""Logging, request logs, security headers and (optional) Sentry.

Nothing here ever records a request body, screenplay text, chat content or an email
address: request logs carry method, path, status, duration and the user id only, and
Sentry events are scrubbed before they leave the process.
"""
import json
import logging
import sys
import time
from urllib.parse import urlparse

from planyourshot.web.settings import get_settings

request_log = logging.getLogger("planyourshot.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


_configured = False


def configure_logging() -> None:
    """One JSON object per line on stdout for the app's own loggers."""
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("planyourshot")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


# ---------- ASGI middleware ----------

class RequestLogMiddleware:
    """Logs one line per HTTP request. Must sit inside SessionMiddleware to see the user."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if not path.startswith("/assets/"):
                session = scope.get("session") or {}
                request_log.info("request", extra={"fields": {
                    "method": scope.get("method"), "path": path, "status": status["code"],
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1), "user_id": session.get("uid"),
                }})


def _origin(dsn: str) -> str | None:
    try:
        u = urlparse(dsn)
        return f"{u.scheme}://{u.hostname}" if u.scheme and u.hostname else None
    except ValueError:
        return None


def content_security_policy() -> str:
    s = get_settings()
    connect = ["'self'"] + sorted({o for o in (_origin(s.sentry_dsn), _origin(s.frontend_sentry_dsn)) if o})
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        # Google Fonts' stylesheet; React sets inline styles through the CSSOM, which CSP allows.
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: https://*.googleusercontent.com",
        "connect-src " + " ".join(connect),
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


class SecurityHeadersMiddleware:
    # The interactive API docs load Swagger UI from a CDN with inline scripts.
    _CSP_EXEMPT = ("/api/docs", "/api/openapi.json")

    def __init__(self, app):
        self.app = app
        s = get_settings()
        self.csp = content_security_policy().encode()
        self.common = [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"x-frame-options", b"DENY"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]
        if s.https:
            self.common.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        exempt = scope.get("path", "").startswith(self._CSP_EXEMPT)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                extra = self.common + ([] if exempt else [(b"content-security-policy", self.csp)])
                headers += [(k, v) for k, v in extra if k not in names]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------- Sentry ----------

_SAFE_HEADERS = {"user-agent", "content-type", "content-length", "accept", "host", "x-request-id"}


def scrub_event(event: dict, _hint=None) -> dict:
    """Remove anything that could carry screenplay text, chat content or an email."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in _SAFE_HEADERS}
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if user.get("id") else {}
    for exc in (event.get("exception") or {}).get("values") or []:
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    event.pop("extra", None)
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict):
        crumbs["values"] = [scrub_breadcrumb(c) for c in crumbs.get("values") or [] if scrub_breadcrumb(c)]
    return event


def scrub_breadcrumb(crumb: dict, _hint=None) -> dict | None:
    if crumb.get("category") in ("console", "ui.input"):
        return None
    data = crumb.get("data")
    if isinstance(data, dict):
        crumb["data"] = {k: v for k, v in data.items() if k in ("method", "url", "status_code", "reason")}
    return crumb


def init_sentry() -> bool:
    s = get_settings()
    if not s.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.sentry_environment,
        send_default_pii=False,  # no IPs, cookies, user emails
        max_request_body_size="never",  # never attach request bodies
        include_local_variables=False,  # locals could hold screenplay text
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        traces_sample_rate=s.sentry_traces_sample_rate,
    )
    return True
