"""Database models. Intentionally small — a singleton Settings row, Projects, and Files."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .db import Base


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    ollama_url = Column(String, default="", nullable=False)
    ollama_api_key = Column(String, default="", nullable=False)
    default_model = Column(String, default="", nullable=False)
    chunk_size = Column(Integer, default=30, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="", nullable=False)
    default_target_lang = Column(String, default="", nullable=False)
    default_model = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    files = relationship(
        "File",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String, nullable=False)
    format = Column(String, nullable=False)  # "srt" | "vtt"
    detected_lang = Column(String, default="", nullable=False)
    target_lang = Column(String, nullable=False)
    model = Column(String, default="", nullable=False)
    # queued | detecting | translating | done | error
    status = Column(String, default="queued", nullable=False)
    progress_pct = Column(Integer, default=0, nullable=False)
    error = Column(Text, default="", nullable=False)
    stored_original_path = Column(String, default="", nullable=False)
    stored_translated_path = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="files")
