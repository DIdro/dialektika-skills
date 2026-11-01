from datetime import datetime
from sqlalchemy import String, Integer, Boolean, ForeignKey, DateTime, Text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    ...


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bearer_token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Веб-логин (email+пароль). Nullable: Telegram/CLI-only юзеры живут без него.
    email: Mapped[str | None] = mapped_column(String)          # уникальность — в коде роутов
    password_hash: Mapped[str | None] = mapped_column(String)  # bcrypt
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Deprecated — kept for backward compat with old DBs. Replaced by the
    # mionika_oauth_* fields below (per-user OAuth credentials).
    mionika_read_token: Mapped[str | None] = mapped_column(Text)
    # Mionika OAuth credentials (per-user, multi-tenant). Synced via
    # `pipeline admin sync-mionika-tokens` from the user's local Code creds.
    mionika_oauth_access_token: Mapped[str | None] = mapped_column(Text)     # encrypted at-rest
    mionika_oauth_refresh_token: Mapped[str | None] = mapped_column(Text)    # encrypted at-rest
    mionika_oauth_expires_at: Mapped[int | None] = mapped_column(Integer)    # unix epoch ms
    mionika_oauth_client_id: Mapped[str | None] = mapped_column(String)
    mionika_oauth_token_endpoint: Mapped[str | None] = mapped_column(String)
    mionika_active_project_keys: Mapped[str | None] = mapped_column(Text)    # JSON array
    context_summary: Mapped[str | None] = mapped_column(Text)
    system_prompt_override: Mapped[str | None] = mapped_column(Text)
    rate_limit_overrides: Mapped[str | None] = mapped_column(Text)           # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)              # short / call
    language: Mapped[str] = mapped_column(String, default="ru", nullable=False)
    diarize: Mapped[bool] = mapped_column(Boolean, nullable=False)
    num_speakers: Mapped[int | None] = mapped_column(Integer, nullable=True)  # hint for pyannote
    dedupe_key: Mapped[str | None] = mapped_column(String, index=True)
    audio_path: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String)  # имя файла как загрузил юзер
    origin: Mapped[str | None] = mapped_column(String)  # zoom | downloads | telegram-desktop | web | telemost | unknown
    audio_size_bytes: Mapped[int | None] = mapped_column(Integer)
    audio_duration_sec: Mapped[float | None] = mapped_column(Float)
    transcript_path: Mapped[str | None] = mapped_column(String)
    draft_path: Mapped[str | None] = mapped_column(String)
    recorded_at: Mapped[str | None] = mapped_column(String)   # ISO дата записи (из имени файла/явно)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String, nullable=False)             # enqueued/started/completed/failed/retry
    stage: Mapped[str] = mapped_column(String, nullable=False)             # whisper/classifier/dispatch
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Recording(Base):
    """A Telemost recording requested via the API (CLI/agent path).

    Lifecycle (driven by worker-recorder): requested → recording → recorded →
    submitted (a mode=call Job created under the same user_id, whose draft then
    lands in that user's the user's drafts) — or failed. `stop_requested` is the
    DB-mediated manual stop: the API flips it, the worker SIGTERMs that
    recording's recorder. Concurrent recordings each get their own display/sink.
    """
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    num_speakers: Mapped[int | None] = mapped_column(Integer)
    quick: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # also Groq quick pass
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display: Mapped[str | None] = mapped_column(String)
    sink: Mapped[str | None] = mapped_column(String)
    audio_path: Mapped[str | None] = mapped_column(String)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    stop_reason: Mapped[str | None] = mapped_column(String)
    job_id: Mapped[str | None] = mapped_column(String)        # diarization Job created on submit
    quick_transcript: Mapped[str | None] = mapped_column(Text)  # --quick: fast Groq pass (no speakers)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DiskState(Base):
    """Singleton row tracked by disk_monitor worker."""
    __tablename__ = "disk_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)             # always 1
    usage_pct: Mapped[float] = mapped_column(Float, nullable=False)
    free_gb: Mapped[float] = mapped_column(Float, nullable=False)
    warning: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
