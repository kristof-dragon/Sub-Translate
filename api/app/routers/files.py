"""File upload, status lookup, download, delete."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, video
from ..db import SessionLocal
from ..worker import job_queue, ocr_queue

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
        # "" for drag-and-drop uploads, populated for files that came through
        # the ffmpeg extraction flow. Consumed by the export UI to decide
        # whether to auto-target the video's folder or prompt for one.
        "source_video_path": f.source_video_path or "",
        # "pgs" for files that went through OCR, "" for files that arrived
        # as text. Lets the UI label OCR-origin rows.
        "source_format": f.source_format or "",
        # Independent progress counter for the OCR phase (0–100). Distinct
        # from `progress_pct` (translation progress) so both phases can
        # render without overwriting each other.
        "ocr_progress_pct": f.ocr_progress_pct or 0,
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

    Works for files in `extracted`, `ocr_done`, `done`, or `error` status —
    typical use is "I just demuxed a subtitle from an MKV, now translate it",
    "OCR finished and I've reviewed the output, translate it now", or "the
    previous run failed, try again". Files currently mid-flight
    (queued/detecting/translating, or anywhere in the OCR pipeline) are
    rejected so we don't double-queue the same job.
    """
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if f.status in ("queued", "detecting", "translating", "ocr_queued", "ocr_running"):
        raise HTTPException(409, f"File is already {f.status}")
    # OCR-origin rows must finish OCR (or be retried) before they can be
    # translated — the on-disk file is still a .sup until OCR runs.
    if f.format not in ("srt", "vtt"):
        raise HTTPException(
            409,
            f"File format is {f.format!r} — finish OCR first via /files/{f.id}/ocr",
        )
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


@router.post("/files/{file_id}/ocr", status_code=202)
async def retry_ocr(file_id: int, db: Session = Depends(get_db)):
    """Re-queue a failed PGS OCR job.

    Only valid for rows that originated from the bitmap-extraction flow
    (`source_format == "pgs"`) and ended up at `ocr_error`. The on-disk
    `.sup` is still where extraction wrote it, so we just reset the
    progress fields and push the file id back onto `ocr_queue`.
    """
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if (f.source_format or "") != "pgs":
        raise HTTPException(409, "OCR retry only applies to PGS-origin files")
    if f.status != "ocr_error":
        raise HTTPException(409, f"Cannot retry OCR from status {f.status!r}")
    if not f.stored_original_path or not os.path.exists(f.stored_original_path):
        raise HTTPException(400, "Source .sup file is missing on disk")

    f.status = "ocr_queued"
    f.ocr_progress_pct = 0
    f.error = ""
    db.commit()
    db.refresh(f)

    await ocr_queue.put(f.id)
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
    # Surrounding whitespace is treated as a typo and stripped — the UI trims
    # anyway, and an operator hitting the API by hand shouldn't be punished
    # for a stray space. Trailing dots are kept rejected though, since some
    # filesystems (FAT, Windows) disallow them outright.
    cleaned = stem.strip()
    if not cleaned:
        raise HTTPException(400, "Name cannot be empty")
    if any(ch in cleaned for ch in _FORBIDDEN_IN_STEM):
        raise HTTPException(400, "Name must not contain path separators or null bytes")
    if cleaned in (".", "..") or cleaned.startswith("."):
        raise HTTPException(400, "Name cannot start with a dot")
    if cleaned.endswith("."):
        raise HTTPException(400, "Name cannot end with a dot")
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


# ---------------------------------------------------------------------------
# Export — copy translated files into the bind-mounted media folder.
# ---------------------------------------------------------------------------

class ExportIn(BaseModel):
    file_ids: list[int] = Field(min_length=1)
    # When None every selected file must have a `source_video_path` — the
    # translation is written alongside its source video. When set, it must
    # resolve to an existing directory inside the media root and every
    # translation goes there, regardless of origin.
    target: Optional[str] = None


def _export_filename(row: models.File) -> str:
    """Output filename for `row` on export — strips the internal `{id}_` prefix.

    The `{id}_` prefix only exists on disk to keep `/data/translated/<pid>/`
    collision-free when multiple files in the same project share a stem. Out
    in the media folder there's no such concern, and the prefix would ruin
    Plex/Jellyfin auto-detection of the matching video.
    """
    current_name = Path(row.stored_translated_path).name
    prefix = f"{row.id}_"
    return current_name[len(prefix):] if current_name.startswith(prefix) else current_name


def _relative_to_media(path: Path) -> str:
    """Display path for API responses — rooted at MEDIA_DIR, forward slashes."""
    try:
        return str(path.relative_to(video.MEDIA_DIR.resolve()))
    except ValueError:
        return str(path)


@router.post("/projects/{project_id}/export")
def export_files(
    project_id: int,
    data: ExportIn,
    db: Session = Depends(get_db),
):
    """Copy translated files out to the media folder.

    Two modes, selected by the `target` field:
      - target=null → "put back next to source video". Requires every file
        to have `source_video_path` set (i.e. originated from the extraction
        flow). Each translation lands in its video's folder.
      - target=<media-relative path> → "pick one folder". Every translation
        lands in that folder, regardless of origin.

    Existing files at the destination are skipped with a `reason="exists"`
    entry on the response rather than overwritten — callers that want
    clobber semantics can delete first and retry.
    """
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    explicit_target: Optional[Path] = None
    if data.target is not None:
        try:
            explicit_target = video.resolve_media_path(data.target)
        except video.MediaPathError as exc:
            raise HTTPException(400, f"Target folder: {exc}")
        if not explicit_target.is_dir():
            raise HTTPException(400, "Target must be a directory")

    rows = (
        db.query(models.File)
        .filter(
            models.File.project_id == project_id,
            models.File.id.in_(data.file_ids),
        )
        .all()
    )
    found_ids = {r.id for r in rows}
    missing = [fid for fid in data.file_ids if fid not in found_ids]
    if missing:
        raise HTTPException(400, f"File IDs not in this project: {missing}")

    written: list[dict] = []
    skipped: list[dict] = []

    for row in rows:
        if row.status != "done":
            skipped.append({
                "file_id": row.id,
                "name": row.original_filename,
                "path": "",
                "reason": f"status is {row.status}",
            })
            continue
        if not row.stored_translated_path or not os.path.exists(row.stored_translated_path):
            skipped.append({
                "file_id": row.id,
                "name": row.original_filename,
                "path": "",
                "reason": "translated file missing on disk",
            })
            continue

        # Destination folder: either the picked target, or the source video's folder.
        if explicit_target is not None:
            dest_dir = explicit_target
        elif row.source_video_path:
            try:
                video_abs = video.resolve_media_path(row.source_video_path)
            except video.MediaPathError as exc:
                skipped.append({
                    "file_id": row.id,
                    "name": row.original_filename,
                    "path": "",
                    "reason": f"source video: {exc}",
                })
                continue
            dest_dir = video_abs.parent
        else:
            skipped.append({
                "file_id": row.id,
                "name": row.original_filename,
                "path": "",
                "reason": "no source video — pick a target folder",
            })
            continue

        out_name = _export_filename(row)
        dest_path = dest_dir / out_name

        if dest_path.exists():
            skipped.append({
                "file_id": row.id,
                "name": out_name,
                "path": _relative_to_media(dest_path),
                "reason": "already exists",
            })
            continue

        try:
            shutil.copy2(row.stored_translated_path, dest_path)
        except OSError as exc:
            skipped.append({
                "file_id": row.id,
                "name": out_name,
                "path": _relative_to_media(dest_path),
                "reason": str(exc),
            })
            continue

        written.append({
            "file_id": row.id,
            "name": out_name,
            "path": _relative_to_media(dest_path),
        })

    return {"written": written, "skipped": skipped}


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
