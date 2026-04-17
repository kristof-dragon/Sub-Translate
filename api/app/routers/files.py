"""File upload, status lookup, download, delete."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..db import SessionLocal
from ..worker import job_queue

router = APIRouter(tags=["files"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTS = {"srt", "vtt"}
MAX_SIZE = 5 * 1024 * 1024  # 5 MB per file


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _project_dir(project_id: int) -> Path:
    """Per-project storage root: /data/uploads/<project_id>/.

    Keeping uploads partitioned by project makes manual cleanup / backup easy
    and avoids a single directory ballooning with every project's files.
    """
    p = UPLOAD_DIR / str(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _display_translated_name(f: models.File) -> str:
    """Filename shown in the UI after translation finishes.

    Derived from the on-disk path so the rename flow can simply update
    `stored_translated_path` and the UI picks up the new name on the next
    payload — no separate column needed.

    The `{file_id}_` auto-prefix (used for uniqueness inside
    `/data/translated/<pid>/`) is stripped here purely for display.
    """
    if not f.stored_translated_path:
        return ""
    name = Path(f.stored_translated_path).name
    prefix = f"{f.id}_"
    if name.startswith(prefix):
        name = name[len(prefix):]
    return name


def _serialize(f: models.File) -> dict:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "original_filename": f.original_filename,
        "format": f.format,
        "detected_lang": f.detected_lang,
        "target_lang": f.target_lang,
        "model": f.model,
        "status": f.status,
        "progress_pct": f.progress_pct,
        "error": f.error,
        "created_at": f.created_at.isoformat(),
        "translated_available": bool(f.stored_translated_path)
        and os.path.exists(f.stored_translated_path),
        "translated_filename": _display_translated_name(f),
    }


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("/projects/{project_id}/files", status_code=201)
async def upload_files(
    project_id: int,
    files: list[UploadFile] = File(...),
    target_lang: str = Form(...),
    model: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    settings = db.get(models.Settings, 1)
    if not settings or not settings.ollama_url:
        raise HTTPException(400, "Ollama not configured — set URL in Settings first")

    effective_model = (model or proj.default_model or settings.default_model or "").strip()
    if not effective_model:
        raise HTTPException(400, "No model selected — pick one in Settings or the project")

    if not target_lang.strip():
        raise HTTPException(400, "target_lang is required")

    created: list[dict] = []

    for upload in files:
        ext = _ext_of(upload.filename or "")
        if ext not in ALLOWED_EXTS:
            raise HTTPException(400, f"Unsupported extension: {upload.filename!r}")
        content = await upload.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(400, f"File too large (max 5 MB): {upload.filename!r}")
        if not content:
            raise HTTPException(400, f"File is empty: {upload.filename!r}")

        row = models.File(
            project_id=project_id,
            original_filename=upload.filename or f"upload.{ext}",
            format=ext,
            target_lang=target_lang.strip(),
            model=effective_model,
            status="queued",
            progress_pct=0,
            stored_original_path="",
        )
        db.add(row)
        db.flush()  # assign id

        stored = _project_dir(project_id) / f"{row.id}_{row.original_filename}"
        stored.write_bytes(content)
        row.stored_original_path = str(stored)
        db.commit()
        db.refresh(row)

        await job_queue.put(row.id)
        created.append(_serialize(row))

    return created


@router.get("/projects/{project_id}/files")
def list_files(project_id: int, db: Session = Depends(get_db)):
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    rows = (
        db.query(models.File)
        .filter(models.File.project_id == project_id)
        .order_by(models.File.created_at.desc())
        .all()
    )
    return [_serialize(r) for r in rows]


@router.get("/files/{file_id}")
def get_file(file_id: int, db: Session = Depends(get_db)):
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return _serialize(f)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: int, db: Session = Depends(get_db)):
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    for p in (f.stored_original_path, f.stored_translated_path):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.delete(f)
    db.commit()


class TranslateIn(BaseModel):
    target_lang: str = Field(min_length=1)
    model: Optional[str] = None


@router.post("/files/{file_id}/translate", status_code=202)
async def translate_file(
    file_id: int,
    data: TranslateIn,
    db: Session = Depends(get_db),
):
    """(Re)queue a file for translation.

    Works for files in `extracted`, `done`, or `error` status — typical use is
    "I just demuxed a subtitle from an MKV, now translate it" or "the previous
    run failed, try again". Files currently mid-flight (queued/detecting/
    translating) are rejected so we don't double-queue the same job.
    """
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if f.status in ("queued", "detecting", "translating"):
        raise HTTPException(409, f"File is already {f.status}")
    if not f.stored_original_path or not os.path.exists(f.stored_original_path):
        raise HTTPException(400, "Original file is missing on disk")

    proj = db.get(models.Project, f.project_id)
    settings = db.get(models.Settings, 1)
    if not settings or not settings.ollama_url:
        raise HTTPException(400, "Ollama not configured — set URL in Settings first")

    effective_model = (
        data.model
        or f.model
        or (proj.default_model if proj else "")
        or settings.default_model
        or ""
    ).strip()
    if not effective_model:
        raise HTTPException(400, "No model selected — pick one in Settings or the project")

    f.target_lang = data.target_lang.strip()
    f.model = effective_model
    f.status = "queued"
    f.progress_pct = 0
    f.error = ""
    f.detected_lang = ""
    f.stored_translated_path = ""
    db.commit()
    db.refresh(f)

    await job_queue.put(f.id)
    return _serialize(f)


@router.get("/files/{file_id}/download")
def download_translated(file_id: int, db: Session = Depends(get_db)):
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if not f.stored_translated_path or not os.path.exists(f.stored_translated_path):
        raise HTTPException(404, "Translated file not ready")
    # Use whatever's on disk (rename edits this) rather than re-deriving from
    # `original_filename`, so a user-chosen name flows to the download prompt.
    download_name = _display_translated_name(f) or (
        f"{Path(f.original_filename).stem}.{f.target_lang}.{f.format}"
    )
    return FileResponse(
        f.stored_translated_path,
        filename=download_name,
        media_type="application/octet-stream",
    )


class RenameIn(BaseModel):
    # The user-supplied stem. Extension (`.{target_lang}.{format}`) is
    # preserved automatically — we never let the client change it so the file
    # stays consistent with what Ollama produced.
    stem: str = Field(min_length=1, max_length=200)


# Characters that would either escape the translated-files directory or create
# a hidden/unreadable file. Rejected outright rather than silently stripped so
# the operator sees what's wrong.
_FORBIDDEN_IN_STEM = ("/", "\\", "\x00")


def _validate_rename_stem(stem: str) -> str:
    cleaned = stem.strip()
    if not cleaned:
        raise HTTPException(400, "Name cannot be empty")
    if any(ch in cleaned for ch in _FORBIDDEN_IN_STEM):
        raise HTTPException(400, "Name must not contain path separators or null bytes")
    if cleaned in (".", "..") or cleaned.startswith("."):
        raise HTTPException(400, "Name cannot start with a dot")
    if cleaned.endswith((".", " ")):
        raise HTTPException(400, "Name cannot end with a dot or space")
    return cleaned


@router.patch("/files/{file_id}/rename")
def rename_translated(
    file_id: int,
    data: RenameIn,
    db: Session = Depends(get_db),
):
    """Rename the translated file on disk.

    Only supported once translation has finished — there's no sensible
    rename semantics for a file that's still being written. The
    `.{target_lang}.{format}` suffix is preserved so Plex/Jellyfin
    auto-detection keeps working.
    """
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if f.status != "done":
        raise HTTPException(409, f"Can only rename completed files (status: {f.status})")
    if not f.stored_translated_path or not os.path.exists(f.stored_translated_path):
        raise HTTPException(404, "Translated file not on disk")

    new_stem = _validate_rename_stem(data.stem)

    current_path = Path(f.stored_translated_path)
    # Preserve `.{lang}.{ext}` regardless of what the user typed — if they
    # included it in the stem we strip it so we don't end up with e.g.
    # "movie.hu.srt.hu.srt".
    suffix = f".{f.target_lang}.{f.format}"
    if new_stem.endswith(suffix):
        new_stem = new_stem[: -len(suffix)]
    new_name = f"{new_stem}{suffix}"

    new_path = current_path.with_name(new_name)
    if new_path == current_path:
        return _serialize(f)  # no-op
    if new_path.exists():
        raise HTTPException(409, f"A file named {new_name!r} already exists here")

    os.replace(current_path, new_path)
    f.stored_translated_path = str(new_path)
    db.commit()
    db.refresh(f)
    return _serialize(f)


@router.get("/files/{file_id}/download/original")
def download_original(file_id: int, db: Session = Depends(get_db)):
    f = db.get(models.File, file_id)
    if not f or not f.stored_original_path or not os.path.exists(f.stored_original_path):
        raise HTTPException(404, "Original file not found")
    return FileResponse(
        f.stored_original_path,
        filename=f.original_filename,
        media_type="application/octet-stream",
    )
