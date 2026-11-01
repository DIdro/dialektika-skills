import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

DATABASE_URL = os.environ.get("PIPELINE_DATABASE_URL", "sqlite:///./pipeline.db")

engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)


# Idempotent ADD COLUMN migrations for tables that already exist in prod —
# create_all() creates missing tables but never ALTERs existing ones. Each entry
# is applied on startup only if the column is absent. SQLite-flavoured.
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "recordings": [("quick_transcript", "TEXT")],
    "jobs": [("recorded_at", "TEXT"), ("original_filename", "TEXT"), ("origin", "TEXT")],
    "users": [
        ("email", "TEXT"),
        ("password_hash", "TEXT"),
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    ],
}


def _ensure_columns() -> None:
    with engine.begin() as conn:
        for table, cols in _COLUMN_MIGRATIONS.items():
            have = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_columns()
