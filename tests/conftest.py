import io
import os
import re

import pytest
from fastapi.testclient import TestClient

from planyourshot.web.ai import (
    AIError, AIService, DiscussResponse, EditOp, EditResponse, ShotFill, ShotFills, ShotRevision, ShotSpec,
)

SCREENPLAY = """THE LAST TRAIN

Written by A. Writer

INT. COFFEE SHOP - NIGHT

SARAH sits alone at a table. She looks at her phone.

A message appears: "WE NEED TO TALK."

JOHN enters, shaking rain from his coat.

JOHN
You came.

SARAH
I almost didn't.

EXT. TRAIN PLATFORM - LATER

Rain hammers the empty platform. Sarah runs for the last train.

SARAH
Wait!

The doors close.
"""


# Stand-in for the model's own on-topic judgement. The real classification is the
# model's job (see the SCOPE rules in web/ai.py); these tests check what the server
# does with the verdict.
_OFF_TOPIC = [
    r"\d+\s*[-+*/x]\s*\d+",  # arithmetic
    r"\bcapital of\b", r"\bwho won\b", r"\bworld cup\b", r"\bweather\b",
    r"\bignore (all |your |the )?(previous )?instructions\b",
    r"\b(python|javascript|sql)\b.*\b(function|script|code)\b|\b(function|script|code)\b.*\b(python|javascript|sql)\b",
]


def judge_on_topic(message: str) -> bool:
    return not any(re.search(p, message, re.I) for p in _OFF_TOPIC)


def leaked_answer(message: str) -> str:
    """What an unguarded model might say: actually answering the question."""
    m = re.search(r"(\d+)\s*\+\s*(\d+)", message)
    if m:
        return f"{int(m[1]) + int(m[2])}. Now, back to the screenplay!"
    return f"Sure! Here is the answer to: {message}"


class FakeAI(AIService):
    """Test double for the model. Records calls; returns deterministic output."""

    def __init__(self):
        self.calls = []
        self.fail = False
        self.edits = [EditOp(find="I almost didn't.", replace="I almost stayed home.", reason="Sharper line")]
        self.shot_ops = []
        self.before_call = None  # optional hook, e.g. to block a call mid-flight
        # The repair call for shots with missing details: "fill" answers (in messy casing, to
        # exercise normalisation), "partial" answers with blanks, "fail" raises, "skip" returns nothing.
        self.repair = "fill"

    def _check(self):
        if self.before_call:
            self.before_call()
        if self.fail:
            raise AIError("The AI service couldn't complete this request. Please try again.")

    def discuss(self, title, context, history, message):
        self._check()
        self.calls.append(("discuss", context, history, message))
        on_topic = judge_on_topic(message)
        return DiscussResponse(on_topic=on_topic, reply=f"Thoughts on {title}: {message}" if on_topic else leaked_answer(message))

    def propose_edits(self, title, context, history, message):
        self._check()
        self.calls.append(("edit", context, history, message))
        if not judge_on_topic(message):
            return EditResponse(on_topic=False, reply=leaked_answer(message), edits=self.edits)
        return EditResponse(on_topic=True, reply="Proposed.", edits=self.edits)

    def scene_shots(self, title, scene_number, scene_text, previous, guidance):
        self._check()
        self.calls.append(("shots", scene_number, guidance))
        return [
            ShotSpec(size="WS", angle="Eye level", movement="Static", lens="24mm", description=f"Establish scene {scene_number}",
                     composition="Subject frame left", characters=["Sarah"], props=["phone"], notes="Geography"),
            # what Ollama was doing: technical fields left empty or as placeholders
            ShotSpec(size="CU", angle="", movement="-", lens="n/a", description="Reaction", composition="", notes=""),
        ]

    def regenerate_shot(self, title, scene_text, scene_shots, target, instruction, guidance):
        self._check()
        self.calls.append(("regenerate", target, instruction))
        return ShotSpec(size="ECU", angle="none", movement="", lens="100 MM", description=f"Regenerated: {instruction}",
                        composition="Tight on the eyes", notes="")

    def complete_shots(self, title, scene_text, shots):
        self.calls.append(("repair", scene_text, shots))
        if self.repair == "fail":
            raise AIError("The AI service couldn't complete this request. Please try again.")
        indices = [int(n) for n in re.findall(r"^\[(\d+)\]", shots, re.M)]
        if self.repair == "skip":
            return ShotFills(shots=[])
        if self.repair == "partial":
            return ShotFills(shots=[ShotFill(index=i, angle="—", movement="none", lens="wide") for i in indices])
        return ShotFills(shots=[ShotFill(index=i, angle="low angle", movement="slow push in", lens="85 MM lens")
                                for i in indices])

    def revise_shots(self, title, context, shots, history, message):
        self._check()
        self.calls.append(("revise", shots, history, message))
        if not judge_on_topic(message):
            return ShotRevision(on_topic=False, reply=leaked_answer(message), operations=self.shot_ops)
        return ShotRevision(on_topic=True, reply="Updated.", operations=self.shot_ops)


# The suite runs on SQLite. Set TEST_DATABASE_URL (e.g. postgresql://pys:pys@localhost/pys_test)
# to run it against Postgres instead; that database is wiped before every test.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


def _wipe_postgres(url):
    from sqlalchemy import create_engine, text

    from planyourshot.web.settings import normalize_database_url

    engine = create_engine(normalize_database_url(url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def env(tmp_path, monkeypatch):
    if TEST_DATABASE_URL:
        _wipe_postgres(TEST_DATABASE_URL)
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTH_DEV_LOGIN", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "nodist"))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    # Jobs run synchronously inside the request that queued them; no worker thread.
    monkeypatch.setenv("RUN_JOBS_INLINE", "1")
    monkeypatch.setenv("RUN_WORKER_IN_WEB", "0")
    monkeypatch.setenv("JOB_RETRY_DELAY_SECONDS", "0")
    for key in ("SENTRY_DSN", "VITE_SENTRY_DSN", "ADMIN_EMAILS", "APP_BASE_URL", "FRONTEND_URL", "LEGAL_DIR",
                "JOB_STALE_SECONDS", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    # Tests run against the built-in plan limits unless they set an override themselves.
    for key in list(os.environ):
        if key.startswith(("FREE_", "PRO_")) or key in (
            "MAX_CONCURRENT_LLM_CALLS", "AUTH_RATE_LIMIT_PER_MINUTE", "UPLOAD_RATE_LIMIT_PER_HOUR",
        ):
            monkeypatch.delenv(key)
    from planyourshot.web import db, ratelimit, settings, usage

    def reset():
        settings.reset_settings()
        db.reset_engine()
        usage.reset_state()
        ratelimit.reset_limiter()

    reset()
    yield
    reset()


@pytest.fixture
def ai():
    return FakeAI()


@pytest.fixture
def migrated(env):
    """The schema, for tests that use the database without a client."""
    from planyourshot.web.db import run_migrations

    run_migrations()


@pytest.fixture
def make_client(env, ai, monkeypatch):
    from planyourshot.web import ai as ai_module
    from planyourshot.web.ai import get_ai
    from planyourshot.web.app import create_app

    app = create_app()
    app.dependency_overrides[get_ai] = lambda: ai
    monkeypatch.setattr(ai_module, "_service", ai)  # what the job worker resolves
    clients = []

    def _make(email=None):
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        if email:
            assert c.post("/api/auth/dev-login", json={"email": email, "name": email}).status_code == 200
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client("a@example.com")


def upload(client, name="the_last_train.txt", data=SCREENPLAY.encode()):
    return client.post("/api/scripts", files={"file": (name, io.BytesIO(data), "application/octet-stream")})


@pytest.fixture
def script(client):
    r = upload(client)
    assert r.status_code == 201, r.text
    return client.get(f"/api/scripts/{r.json()['id']}").json()


def set_user(email, **fields):
    """Change a user row directly, e.g. set_user("a@example.com", plan="pro")."""
    from sqlalchemy import select

    from planyourshot.web.db import new_session
    from planyourshot.web.models import User

    db = new_session()
    try:
        user = db.scalar(select(User).where(User.email == email))
        for k, v in fields.items():
            setattr(user, k, v)
        db.commit()
    finally:
        db.close()


def usage_events():
    from sqlalchemy import select

    from planyourshot.web.db import new_session
    from planyourshot.web.models import UsageEvent

    db = new_session()
    try:
        return list(db.scalars(select(UsageEvent).order_by(UsageEvent.id)))
    finally:
        db.close()


def age_events(seconds):
    """Move every usage event `seconds` into the past (to test rolling windows)."""
    from datetime import timedelta

    from planyourshot.web.db import new_session
    from planyourshot.web.models import UsageEvent

    db = new_session()
    try:
        for ev in db.query(UsageEvent):
            ev.created_at -= timedelta(seconds=seconds)
        db.commit()
    finally:
        db.close()
