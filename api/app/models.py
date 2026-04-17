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

    # 1 = after PGS OCR completes, run each cue's text through Ollama with a
    # "fix OCR errors" prompt before the row is offered for translation.
    # Doubles OCR wall-clock time but corrects common confusions (l/I, 0/O,
    # missing diacritics) that hurt downstream translation quality.
    ocr_llm_cleanup = Column(Integer, default=0, nullable=False)

    # Model name used for the OCR cleanup pass. Empty = fall back to
    # `default_model`. Kept separate so the operator can route cleanup at a
    # smaller / cheaper model than the one doing translation.
    ocr_llm_model = Column(String, default="", nullable=False)


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
    # Current translatable format on disk. Bitmap origins start as "pgs" and
    # flip to "srt" once OCR has emitted the text version.
    format = Column(String, nullable=False)  # "srt" | "vtt" | "pgs"
    detected_lang = Column(String, default="", nullable=False)
    target_lang = Column(String, nullable=False)
    model = Column(String, default="", nullable=False)
    # extracting | extracted | ocr_queued | ocr_running | ocr_done | ocr_error
    # | queued | detecting | translating | done | error
    # "extracting"  = ffmpeg demux is queued/in-flight (async extraction worker).
    # "extracted"   = demux finished; raw subtitle on disk, not yet translated.
    # "ocr_queued"  = bitmap subtitle extracted, awaiting OCR worker.
    # "ocr_running" = OCR in flight; ocr_progress_pct tracks per-cue progress.
    # "ocr_done"    = OCR finished; row is now an SRT, awaiting manual Translate.
    # "ocr_error"   = OCR failed; operator can re-queue via /files/{id}/ocr.
    # All other statuses are unchanged from before OCR support landed — the
    # translate worker only ever sees text formats post-OCR.
    status = Column(String, default="queued", nullable=False)
    progress_pct = Column(Integer, default=0, nullable=False)
    error = Column(Text, default="", nullable=False)
    stored_original_path = Column(String, default="", nullable=False)
    stored_translated_path = Column(String, default="", nullable=False)
    # Absolute path to the video this subtitle was extracted from, or "" for
    # drag-and-drop uploads. Used by the "put back next to source video"
    # export option so we know where to copy the translated file to.
    source_video_path = Column(String, default="", nullable=False)
    # "pgs" for bitmap-source files that went through OCR, "" for files that
    # arrived as text. Lets the OCR worker dispatch on origin and the UI
    # label rows that came in via OCR. Stays set after OCR completes — the
    # `format` column flips to "srt" but `source_format` records where the
    # SRT came from.
    source_format = Column(String, default="", nullable=False)
    # Per-stage progress counter for the OCR phase. Distinct from
    # `progress_pct` (which tracks translation) so both phases can show
    # independent progress in the UI without one overwriting the other.
    ocr_progress_pct = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="files")
