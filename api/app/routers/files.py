"""File upload, status lookup, download, delete."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
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

        stored = UPLOAD_DIR / f"{row.id}_{row.original_filename}"
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


@router.get("/files/{file_id}/download")
def download_translated(file_id: int, db: Session = Depends(get_db)):
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if not f.stored_translated_path or not os.path.exists(f.stored_translated_path):
        raise HTTPException(404, "Translated file not ready")
    stem = Path(f.original_filename).stem
    download_name = f"{stem}.{f.target_lang}.{f.format}"
    return FileResponse(
        f.stored_translated_path,
        filename=download_name,
        media_type="application/octet-stream",
    )


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
