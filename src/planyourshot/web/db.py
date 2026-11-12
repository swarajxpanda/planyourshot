from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from planyourshot.web.settings import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None

# Arbitrary constant: the Postgres advisory lock held while migrations run, so the
# web and worker processes can both start with AUTO_MIGRATE without racing.
_MIGRATION_LOCK = 72_311_001


def is_postgres(engine: Engine | None = None) -> bool:
    return (engine or get_engine()).dialect.name == "postgresql"


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        s = get_settings()
        url = s.database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # Background work runs in other threads, and SQLite waits up to `timeout`
            # seconds for the single writer lock instead of failing at once.
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if ":///" in url and ":memory:" not in url:
                Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        else:
            kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_recycle=1800,
                          pool_timeout=30)
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _pragmas(conn, _):  # pragma: no cover - trivial
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def new_session() -> Session:
    get_engine()
    assert _Session is not None
    return _Session()


def get_db() -> Iterator[Session]:
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


def ping() -> None:
    """Raise if the database can't be reached."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    engine = get_engine()  # also makes sure the sqlite directory exists
    if not is_postgres(engine):
        command.upgrade(cfg, "head")
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK})
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK})
            conn.commit()
