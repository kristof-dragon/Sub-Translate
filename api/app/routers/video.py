"""Video browse / subtitle track list / extract-to-project endpoints.

Unlike the previous mkv router, /extract does NOT queue a translation —
extracted tracks land in the project folder with status="extracted" and can
later be translated on demand via POST /api/files/{id}/translate.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, video
from ..db import SessionLocal

router = APIRouter(tags=["video"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _project_dir(project_id: int) -> Path:
    """Per-project storage root: /data/uploads/<project_id>/."""
    p = UPLOAD_DIR / str(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _serialize_file(f: models.File) -> dict:
    # Same shape as routers/files.py.
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


@router.get("/browse")
def browse(path: str = Query(default="")):
    try:
        return video.browse(path)
    except video.MediaPathError as exc:
        raise HTTPException(400, str(exc))


@router.get("/video/tracks")
def video_tracks(path: str = Query(...)):
    try:
        tracks = video.list_video_tracks(path)
    except video.MediaPathError as exc:
        raise HTTPException(400, str(exc))
    return {"tracks": [t.__dict__ for t in tracks]}


class ExtractIn(BaseModel):
    video_path: str = Field(min_length=1)
    track_ids: list[int] = Field(min_length=1)


@router.post("/projects/{project_id}/extract", status_code=201)
def extract_to_project(
    project_id: int,
    data: ExtractIn,
    db: Session = Depends(get_db),
):
    """Extract subtitle tracks into the project — no translation yet.

    Each extracted track becomes a File row in `extracted` status. Operators
    can then download the raw subtitle, or click Translate on the row to
    kick off the detect → translate → write pipeline.
    """
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    # Probe once so we can validate every requested track before any extraction.
    try:
        tracks = video.list_video_tracks(data.video_path)
    except video.MediaPathError as exc:
        raise HTTPException(400, str(exc))

    tracks_by_id = {t.id: t for t in tracks}
    for tid in data.track_ids:
        t = tracks_by_id.get(tid)
        if t is None:
            raise HTTPException(400, f"Track {tid} not found in {data.video_path}")
        if not t.supported:
            raise HTTPException(
                400,
                f"Track {tid} ({t.codec}) is not a translatable text subtitle format",
            )

    stem = Path(data.video_path).stem
    proj_dir = _project_dir(project_id)
    created: list[dict] = []

    for tid in data.track_ids:
        track = tracks_by_id[tid]
        assert track.ext is not None  # enforced above

        lang_tag = f".{track.language}" if track.language else ""
        filename = f"{stem}{lang_tag}.stream{tid}.{track.ext}"

        row = models.File(
            project_id=project_id,
            original_filename=filename,
            format=track.ext,
            target_lang="",  # not translating yet — Translate button provides this later
            model="",
            status="extracted",
            progress_pct=0,
            stored_original_path="",
        )
        db.add(row)
        db.flush()

        out_path = proj_dir / f"{row.id}_{filename}"
        try:
            video.extract_track(data.video_path, tid, out_path)
        except video.MediaPathError as exc:
            db.delete(row)
            db.commit()
            raise HTTPException(500, f"Extraction failed: {exc}")

        row.stored_original_path = str(out_path)
        db.commit()
        db.refresh(row)
        created.append(_serialize_file(row))

    return created
