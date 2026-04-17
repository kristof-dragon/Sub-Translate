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

    # 1 = send `"think": false` with /api/generate so reasoning-capable models
    # (deepseek-r1, qwq, gpt-oss, …) skip the internal monologue. 0 = omit the
    # field, which is how Ollama's own default behaviour is reached.
    disable_thinking = Column(Integer, default=0, nullable=False)

    # HTTP timeout applied to the Ollama httpx client. Translation chunks can
    # take a long time on big local models, hence the 10-min default.
    request_timeout = Column(Integer, default=600, nullable=False)

    # Optional override of the model's context window (Ollama `options.num_ctx`).
    # 0 = don't send it; let Ollama pick the model default. Surfaced in the UI
    # so the operator can see whether a context value is attached to each call.
    num_ctx = Column(Integer, default=0, nullable=False)


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
    # extracting | extracted | queued | detecting | translating | done | error
    # "extracting" = ffmpeg demux is queued/in-flight (async extraction worker).
    # "extracted"  = demux finished; raw subtitle on disk, not yet translated.
    #                A follow-up POST /files/{id}/translate moves it to "queued".
    status = Column(String, default="queued", nullable=False)
    progress_pct = Column(Integer, default=0, nullable=False)
    error = Column(Text, default="", nullable=False)
    stored_original_path = Column(String, default="", nullable=False)
    stored_translated_path = Column(String, default="", nullable=False)
    # Absolute path to the video this subtitle was extracted from, or "" for
    # drag-and-drop uploads. Used by the "put back next to source video"
    # export option so we know where to copy the translated file to.
    source_video_path = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="files")
