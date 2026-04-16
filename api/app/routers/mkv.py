"""MKV browse / track list / extract-to-project endpoints."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, mkv
from ..db import SessionLocal
from ..worker import job_queue

router = APIRouter(tags=["mkv"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/browse")
def browse(path: str = Query(default="")):
    try:
        return mkv.browse(path)
    except mkv.MediaPathError as exc:
        raise HTTPException(400, str(exc))


@router.get("/mkv/tracks")
def mkv_tracks(path: str = Query(...)):
    try:
        tracks = mkv.list_mkv_tracks(path)
    except mkv.MediaPathError as exc:
        raise HTTPException(400, str(exc))
    return {"tracks": [t.__dict__ for t in tracks]}


class FromMkvIn(BaseModel):
    mkv_path: str = Field(min_length=1)
    track_ids: list[int] = Field(min_length=1)
    target_lang: str = Field(min_length=1)
    model: Optional[str] = None


def _serialize_file(f: models.File) -> dict:
    # Same shape as routers/files.py — kept in sync manually since both ship
    # to the same frontend FileRow component.
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


@router.post("/projects/{project_id}/from-mkv", status_code=201)
async def extract_from_mkv(
    project_id: int,
    data: FromMkvIn,
    db: Session = Depends(get_db),
):
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    settings = db.get(models.Settings, 1)
    if not settings or not settings.ollama_url:
        raise HTTPException(400, "Ollama not configured — set URL in Settings first")

    effective_model = (
        data.model or proj.default_model or settings.default_model or ""
    ).strip()
    if not effective_model:
        raise HTTPException(400, "No model selected — pick one in Settings or the project")

    # Verify the MKV + cache the supported-track map in one shot so we fail
    # fast on unsupported selections before any extraction runs.
    try:
        tracks = mkv.list_mkv_tracks(data.mkv_path)
    except mkv.MediaPathError as exc:
        raise HTTPException(400, str(exc))

    tracks_by_id = {t.id: t for t in tracks}
    for tid in data.track_ids:
        t = tracks_by_id.get(tid)
        if t is None:
            raise HTTPException(400, f"Track {tid} not found in {data.mkv_path}")
        if not t.supported:
            raise HTTPException(
                400,
                f"Track {tid} ({t.codec_id}) is not a supported text subtitle format",
            )

    created: list[dict] = []
    mkv_stem = Path(data.mkv_path).stem

    for tid in data.track_ids:
        track = tracks_by_id[tid]
        assert track.ext is not None  # enforced by the supported check above

        # Create the row first so we get an id to embed in the filename —
        # keeps per-file storage collision-free across projects.
        lang_tag = f".{track.language}" if track.language else ""
        filename = f"{mkv_stem}{lang_tag}.track{tid}.{track.ext}"
        row = models.File(
            project_id=project_id,
            original_filename=filename,
            format=track.ext,
            target_lang=data.target_lang.strip(),
            model=effective_model,
            status="queued",
            progress_pct=0,
            stored_original_path="",
        )
        db.add(row)
        db.flush()

        out_path = UPLOAD_DIR / f"{row.id}_{filename}"
        try:
            mkv.extract_track(data.mkv_path, tid, out_path)
        except mkv.MediaPathError as exc:
            db.delete(row)
            db.commit()
            raise HTTPException(500, f"Extraction failed: {exc}")

        row.stored_original_path = str(out_path)
        db.commit()
        db.refresh(row)

        await job_queue.put(row.id)
        created.append(_serialize_file(row))

    return created
