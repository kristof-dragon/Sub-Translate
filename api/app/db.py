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

    with SessionLocal() as db:
        if not db.get(models.Settings, 1):
            db.add(models.Settings(id=1))
            db.commit()
