"""SQLAlchemy engine, session, and table initialisation."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/app.db")

# SQLite-specific: allow sharing connection across threads (FastAPI threadpool).
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def init_db() -> None:
    """Create tables on first boot and ensure the singleton Settings row exists."""
    if DATABASE_URL.startswith("sqlite:///"):
        path = DATABASE_URL.replace("sqlite:///", "", 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    # Import here so model classes register with Base before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()

    with SessionLocal() as db:
        if not db.get(models.Settings, 1):
            db.add(models.Settings(id=1))
            db.commit()


# Columns added to existing tables after v1.0. SQLAlchemy's create_all() only
# creates missing tables, not missing columns, so we ALTER TABLE ADD COLUMN
# here for anything that's landed since the DB was first seeded. SQLite is
# happy with this as long as every add has a concrete DEFAULT.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "settings": [
        ("disable_thinking", "INTEGER NOT NULL DEFAULT 0"),
        ("request_timeout", "INTEGER NOT NULL DEFAULT 600"),
        ("num_ctx", "INTEGER NOT NULL DEFAULT 0"),
        ("ocr_llm_cleanup", "INTEGER NOT NULL DEFAULT 0"),
        ("ocr_llm_model", "TEXT NOT NULL DEFAULT ''"),
    ],
    "files": [
        ("source_video_path", "TEXT NOT NULL DEFAULT ''"),
        ("source_format", "TEXT NOT NULL DEFAULT ''"),
        ("ocr_progress_pct", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def _apply_additive_migrations() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return  # non-SQLite deployments aren't supported yet; bail safely.
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, spec in columns:
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {spec}"
                    )
