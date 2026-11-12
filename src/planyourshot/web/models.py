import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from planyourshot.web.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    plan: Mapped[str] = mapped_column(String(16), default="free", server_default="free")  # see web/limits.py
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # When the user agreed to the Terms and Privacy Policy (set on sign-in if unset).
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))
    file_size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing|ready|failed
    error: Mapped[str] = mapped_column(Text, default="")
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shotlist_status: Mapped[str] = mapped_column(String(16), default="none")  # none|generating|ready|failed
    shotlist_error: Mapped[str] = mapped_column(Text, default="")
    shotlist_done: Mapped[int] = mapped_column(Integer, default=0)
    shotlist_total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="script", cascade="all, delete-orphan", order_by="Revision.number"
    )
    messages: Mapped[list["Message"]] = relationship(cascade="all, delete-orphan")
    proposals: Mapped[list["Proposal"]] = relationship(cascade="all, delete-orphan")
    shots: Mapped[list["Shot"]] = relationship(cascade="all, delete-orphan")


class Revision(Base):
    __tablename__ = "revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))  # upload|ai_edit|manual|restore
    summary: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    script: Mapped[Script] = relationship(back_populates="revisions")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(8))  # script|shots
    role: Mapped[str] = mapped_column(String(10))  # user|assistant
    mode: Mapped[str] = mapped_column(String(8), default="discuss")  # discuss|edit
    content: Mapped[str] = mapped_column(Text)
    # Off-topic exchange: shown to the user, never sent back to the model as history.
    refused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_messages_script_channel", "script_id", "channel", "id"),)


class Proposal(Base):
    """A set of AI-suggested screenplay edits awaiting the user's decision."""

    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    base_revision_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|applied|rejected
    # [{find, replace, reason, status: pending|applied|rejected|failed}]
    edits: Mapped[list] = mapped_column(JSON)
    result_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Shot(Base):
    __tablename__ = "shots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"))
    scene_number: Mapped[int] = mapped_column(Integer)
    scene_heading: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(Integer)
    size: Mapped[str] = mapped_column(String(8), default="MS")
    angle: Mapped[str] = mapped_column(String(80), default="")
    movement: Mapped[str] = mapped_column(String(80), default="")
    lens: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    composition: Mapped[str] = mapped_column(Text, default="")
    characters: Mapped[list] = mapped_column(JSON, default=list)
    props: Mapped[list] = mapped_column(JSON, default=list)
    location: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_shots_script_scene", "script_id", "scene_number", "position"),)


class UsageEvent(Base):
    """One row per model call (plus one per whole shot-list run). Quotas are counted from here."""

    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # No FK: usage history outlives a deleted screenplay.
    script_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # chat|edit|shot_chat|shot_generate_full|shot_generate_scene|shot_regenerate
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(10))  # ok|failed|refused
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    output_chars: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Scene events inside a full shot-list run point at that run's shot_generate_full
    # event, so they are kept for cost tracking but not counted as single-scene runs.
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_usage_user_kind_created", "user_id", "kind", "created_at"),)


class Job(Base):
    """Durable background work, run by the worker (web/worker.py)."""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))  # process_upload|generate_shots
    # No FK: a job outlives its screenplay long enough to be marked cancelled.
    script_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued|running|done|failed|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    # Not before this time (retry back-off).
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # heartbeat while running
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # claim token of the running worker
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_jobs_status_available", "status", "available_at", "id"),)


class RateLimitHit(Base):
    """One row per request counted by the database rate limiter (web/ratelimit.py)."""

    __tablename__ = "rate_limit_hits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_rate_limit_hits_key_created", "key", "created_at"),)


class Lease(Base):
    """A named lock that expires (web/leases.py): the one-call-per-user lock and the
    slots of the global model-call cap. A crashed process's leases simply run out."""

    __tablename__ = "leases"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
