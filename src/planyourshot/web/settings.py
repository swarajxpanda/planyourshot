"""Web-app settings, all read from the environment (see .env.example)."""
import logging
import os
from pathlib import Path

from planyourshot import config as _core  # also loads .env

REPO_ROOT = Path(__file__).resolve().parents[3]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url: str) -> str:
    """Hosts hand out postgres:// and postgresql:// URLs; SQLAlchemy needs the
    driver named, and this app uses psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else (Path.cwd() / p)


class Settings:
    def __init__(self) -> None:
        self.database_url = normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///data/app.db"))
        # Connection pool (Postgres only; SQLite uses SQLAlchemy's defaults).
        self.db_pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        self.db_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "5"))
        self.storage_dir = _path("STORAGE_DIR", "data/uploads")
        self.session_secret = os.getenv("SESSION_SECRET", "")
        self.google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        # Public URL of the backend (used for the OAuth redirect URI).
        self.app_base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        # Where to send the browser after login. Same as the backend in
        # production; the Vite dev server (http://localhost:5173) in development.
        self.frontend_url = os.getenv("FRONTEND_URL", self.app_base_url).rstrip("/")
        # Screenplay count/length and model quotas are per plan: see web/limits.py.
        if os.getenv("MAX_SCRIPTS_PER_USER"):
            logging.getLogger("planyourshot").warning(
                "MAX_SCRIPTS_PER_USER is no longer used; set FREE_MAX_SCRIPTS / PRO_MAX_SCRIPTS instead.")
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "10"))
        # Model call guards.
        self.llm_timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
        self.llm_max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000"))
        self.max_concurrent_llm_calls = max(1, int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "2")))
        # Rate limits on non-model routes.
        self.auth_rate_limit_per_minute = int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "20"))
        self.upload_rate_limit_per_hour = int(os.getenv("UPLOAD_RATE_LIMIT_PER_HOUR", "10"))
        # Screenplays longer than this are sent to the model as outline +
        # retrieved scenes instead of in full.
        # With Ollama the default is sized to the model's context window
        # (~3.5 chars per token, leaving room for the prompt, chat and reply).
        fits = max(8000, int((_core.OLLAMA_NUM_CTX - 4000) * 3.5)) if _core.LLM_PROVIDER == "ollama" else 300_000
        self.max_context_chars = int(os.getenv("MAX_CONTEXT_CHARS", str(fits)))
        # Local development only: sign in without Google. Never enable in production.
        self.auth_dev_login = _bool("AUTH_DEV_LOGIN", False)
        self.auto_migrate = _bool("AUTO_MIGRATE", True)
        # Background jobs (web/jobs.py, web/worker.py).
        self.job_stale_seconds = int(os.getenv("JOB_STALE_SECONDS", "600"))
        self.job_poll_seconds = float(os.getenv("JOB_POLL_SECONDS", "2"))
        self.job_retry_delay_seconds = float(os.getenv("JOB_RETRY_DELAY_SECONDS", "15"))
        # Run the worker loop in a thread of the web process. Default on for SQLite
        # (local development, `uvicorn` alone works), off for Postgres (run the worker).
        self.run_worker_in_web = _bool("RUN_WORKER_IN_WEB", self.is_sqlite)
        # Run each job synchronously right after it is enqueued (tests only).
        self.run_jobs_inline = _bool("RUN_JOBS_INLINE", False)
        # Monitoring and administration.
        self.sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
        self.sentry_environment = os.getenv("SENTRY_ENVIRONMENT", "production" if self.https else "development")
        self.sentry_traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0"))
        # The frontend's DSN is baked in at build time; the backend only needs it for the CSP.
        self.frontend_sentry_dsn = os.getenv("VITE_SENTRY_DSN", "").strip()
        self.admin_emails = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
        self.legal_dir = _path("LEGAL_DIR", str(REPO_ROOT / "legal"))
        self.knowledge_dir = self._knowledge_dir()
        self.frontend_dist = _path("FRONTEND_DIST", str(REPO_ROOT / "frontend" / "dist"))

    @staticmethod
    def _knowledge_dir() -> Path:
        p = Path(os.getenv("KNOWLEDGE_DIR", "data/knowledge"))
        if p.is_absolute() or (Path.cwd() / p).exists():
            return p if p.is_absolute() else Path.cwd() / p
        return REPO_ROOT / p

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def https(self) -> bool:
        return self.app_base_url.startswith("https://")

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
