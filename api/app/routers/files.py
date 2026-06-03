"""File upload, status lookup, download, delete."""
from __future__ import annotations

import json
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
from ..subtitles import merge, srt, vtt
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
        # Track title (or "stream N") of the source subtitle stream for
        # extracted rows; "" for uploads/merged. Rendered as a tag in the list.
        "source_track_name": f.source_track_name or "",
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
    """Re-queue a failed bitmap OCR job.

    Only valid for rows that originated from the bitmap-extraction flow
    (`source_format` in `pgs`/`vobsub`) and ended up at `ocr_error`. The
    on-disk bitmap is still where extraction wrote it, so we just reset
    the progress fields and push the file id back onto `ocr_queue`.
    """
    f = db.get(models.File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if (f.source_format or "") not in ("pgs", "vobsub"):
        raise HTTPException(409, "OCR retry only applies to bitmap-origin files")
    if f.status != "ocr_error":
        raise HTTPException(409, f"Cannot retry OCR from status {f.status!r}")
    if not f.stored_original_path or not os.path.exists(f.stored_original_path):
        raise HTTPException(400, "Source bitmap file is missing on disk")

    f.status = "ocr_queued"
    f.ocr_progress_pct = 0
    f.error = ""
    db.commit()
    db.refresh(f)

    await ocr_queue.put(f.id)
    return _serialize(f)


# ---------------------------------------------------------------------------
# Merge — stitch a "forced" + a "standard" subtitle into one union subtitle.
# ---------------------------------------------------------------------------


def _parse_index_set(raw: Optional[str], field: str) -> set[int]:
    """Parse a JSON array of non-negative ints (overlap-cluster indices).

    None / "" / "[]" all mean "empty set". Anything else that isn't a list of
    ints is a 400 so a malformed client payload fails loudly rather than being
    silently ignored (which would merge the wrong cues).
    """
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"{field} must be a JSON array of integers") from exc
    if not isinstance(data, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in data
    ):
        raise HTTPException(400, f"{field} must be a JSON array of non-negative integers")
    return set(data)


async def _load_slot_cues(
    db: Session,
    project_id: int,
    file_id: Optional[int],
    upload: Optional[UploadFile],
    used_ids: set[int],
) -> tuple[list, str, str]:
    """Resolve one merge slot to (cues, source_stem, source_video_path).

    A slot is EITHER an existing project file (`file_id`) OR a freshly uploaded
    `.srt`/`.vtt` (`upload`) — exactly one. Uploaded bytes are merge-only inputs
    and never create their own File row (so they don't get auto-translated like
    a normal dropzone upload would).
    """
    has_id = file_id is not None
    has_upload = upload is not None and bool(upload.filename)
    if has_id == has_upload:
        raise HTTPException(
            400, "Each merge slot needs exactly one of an existing file or an upload"
        )

    if has_id:
        if file_id in used_ids:
            raise HTTPException(400, "Pick two different files to merge")
        used_ids.add(file_id)
        row = db.get(models.File, file_id)
        if not row or row.project_id != project_id:
            raise HTTPException(404, f"File {file_id} not found in this project")
        if row.format not in ("srt", "vtt"):
            raise HTTPException(
                409,
                f"File {file_id} is {row.format!r} — only text (srt/vtt) "
                "subtitles can be merged; finish OCR first if it's a bitmap track",
            )
        if not row.stored_original_path or not os.path.exists(row.stored_original_path):
            raise HTTPException(400, f"File {file_id} has no source file on disk")
        content = Path(row.stored_original_path).read_text(
            encoding="utf-8-sig", errors="replace"
        )
        fmt = row.format
        stem = Path(row.original_filename).stem
        src_video = row.source_video_path or ""
    else:
        ext = _ext_of(upload.filename or "")
        if ext not in ALLOWED_EXTS:
            raise HTTPException(400, f"Unsupported extension: {upload.filename!r}")
        data = await upload.read()
        if len(data) > MAX_SIZE:
            raise HTTPException(400, f"File too large (max 5 MB): {upload.filename!r}")
        if not data:
            raise HTTPException(400, f"File is empty: {upload.filename!r}")
        content = data.decode("utf-8-sig", errors="replace")
        fmt = ext
        stem = Path(upload.filename or f"upload.{ext}").stem
        src_video = ""

    cues = srt.parse_srt(content) if fmt == "srt" else vtt.parse_vtt(content)
    if not cues:
        raise HTTPException(400, "No cues found in one of the files")
    return cues, stem, src_video


@router.post("/projects/{project_id}/merge/preview")
async def merge_preview(
    project_id: int,
    file_id_a: Optional[int] = Form(None),
    file_id_b: Optional[int] = Form(None),
    upload_a: Optional[UploadFile] = File(None),
    upload_b: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Overlap report for two subtitle slots — creates nothing on disk or in DB."""
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    used: set[int] = set()
    cues_a, stem_a, _va = await _load_slot_cues(db, project_id, file_id_a, upload_a, used)
    cues_b, stem_b, _vb = await _load_slot_cues(db, project_id, file_id_b, upload_b, used)

    out = merge.report_to_dict(merge.analyze(cues_a, cues_b))
    out["default_name"] = merge.default_name(stem_a, stem_b)
    return out


@router.post("/projects/{project_id}/merge", status_code=201)
async def merge_commit(
    project_id: int,
    file_id_a: Optional[int] = Form(None),
    file_id_b: Optional[int] = Form(None),
    upload_a: Optional[UploadFile] = File(None),
    upload_b: Optional[UploadFile] = File(None),
    output_name: Optional[str] = Form(None),
    # JSON arrays of 0-based overlap-cluster indices whose forced / full side the
    # operator unticked in the overlap report. Empty/omitted = keep everything
    # (combine all clashes), i.e. the default behaviour.
    drop_forced: Optional[str] = Form(None),
    drop_full: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Merge two subtitle slots into one SRT row at status `extracted`.

    The merged row behaves exactly like a freshly-extracted text track: it rests
    until the operator clicks Translate. `source_format="merged"` lets the UI
    label it; no auto-queue, no new pipeline state.
    """
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    used: set[int] = set()
    cues_a, stem_a, vid_a = await _load_slot_cues(db, project_id, file_id_a, upload_a, used)
    cues_b, stem_b, vid_b = await _load_slot_cues(db, project_id, file_id_b, upload_b, used)

    report = merge.analyze(cues_a, cues_b)
    merged_cues = merge.combine(
        cues_a,
        cues_b,
        drop_forced=_parse_index_set(drop_forced, "drop_forced"),
        drop_full=_parse_index_set(drop_full, "drop_full"),
    )

    requested = (output_name or "").strip()
    if requested.endswith(".srt"):
        requested = requested[:-4]
    stem = _validate_rename_stem(requested) if requested else merge.default_name(stem_a, stem_b)

    # Carry the source video only when both slots came from the *same* video, so
    # the "export next to source video" flow still has an unambiguous target.
    src_video = vid_a if (vid_a and vid_a == vid_b) else ""

    # Mirror upload_files' ordering for atomicity: flush to get the id, write the
    # file using that id, then commit — a failed write aborts before commit.
    row = models.File(
        project_id=project_id,
        original_filename=f"{stem}.srt",
        format="srt",
        target_lang="",
        model="",
        status="extracted",
        progress_pct=100,
        stored_original_path="",
        source_video_path=src_video,
        source_format="merged",
    )
    db.add(row)
    db.flush()  # assign id

    out_path = _project_dir(project_id) / f"{row.id}_{stem}.srt"
    out_path.write_text(srt.write_srt(merged_cues), encoding="utf-8")
    row.stored_original_path = str(out_path)
    db.commit()
    db.refresh(row)

    result = _serialize(row)
    result["overlap_count"] = report.overlap_count
    result["result_cues"] = report.result_cues
    return result


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

class ExportItemIn(BaseModel):
    file_id: int
    # Which on-disk file to export for this row:
    #   "translated" → stored_translated_path (requires a finished translation)
    #   "source"     → stored_original_path (the extracted / uploaded / *merged
    #                  unified* subtitle — exportable even before translation)
    version: str = "translated"


class ExportIn(BaseModel):
    items: list[ExportItemIn] = Field(min_length=1)
    # When None every item must have a `source_video_path` — the file is written
    # alongside its source video. When set, it must resolve to an existing
    # directory inside the media root and every file goes there.
    target: Optional[str] = None


def _export_filename(row: models.File, version: str) -> str:
    """Output filename for `row`/`version` on export — strips the `{id}_` prefix.

    The `{id}_` prefix only exists on disk to keep the per-project folders
    collision-free; out in the media folder it would ruin Plex/Jellyfin
    auto-detection. We derive the name from the *actual file being exported*
    (not `original_filename`) so an OCR'd source exports as `.srt`, not the
    stale `.sup` the row is still named after.
    """
    path = row.stored_translated_path if version == "translated" else row.stored_original_path
    current_name = Path(path).name
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
    """Copy subtitle files out to the media folder.

    Each item names a row and a `version` ("translated" or "source"). The
    destination is chosen by `target`:
      - target=null → "put back next to source video". Each item lands in its
        video's folder; an item without `source_video_path` is skipped.
      - target=<media-relative path> → "pick one folder". Every item goes there.

    `version="source"` exports the extracted / uploaded / *merged unified*
    subtitle and does NOT require a finished translation — only that the source
    file exists on disk. Existing files at the destination are skipped (no
    overwrite) rather than clobbered.
    """
    proj = db.get(models.Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    version_by_id: dict[int, str] = {}
    for it in data.items:
        if it.version not in ("translated", "source"):
            raise HTTPException(400, f"Unknown version {it.version!r} for file {it.file_id}")
        version_by_id[it.file_id] = it.version

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
            models.File.id.in_(list(version_by_id.keys())),
        )
        .all()
    )
    found_ids = {r.id for r in rows}
    missing = [fid for fid in version_by_id if fid not in found_ids]
    if missing:
        raise HTTPException(400, f"File IDs not in this project: {missing}")

    written: list[dict] = []
    skipped: list[dict] = []

    def skip(row: models.File, reason: str, path: str = "", name: str = "") -> None:
        skipped.append({
            "file_id": row.id,
            "name": name or row.original_filename,
            "path": path,
            "reason": reason,
        })

    for row in rows:
        version = version_by_id[row.id]

        # Resolve the source-of-truth file for the requested version.
        if version == "translated":
            src_path = row.stored_translated_path
            if row.status != "done":
                skip(row, f"no translation yet (status is {row.status})")
                continue
            if not src_path or not os.path.exists(src_path):
                skip(row, "translated file missing on disk")
                continue
        else:  # "source" — the extracted/uploaded/merged subtitle, pre-translation
            src_path = row.stored_original_path
            if not src_path or not os.path.exists(src_path):
                skip(row, "source file missing on disk")
                continue

        # Destination folder: either the picked target, or the source video's folder.
        if explicit_target is not None:
            dest_dir = explicit_target
        elif row.source_video_path:
            try:
                video_abs = video.resolve_media_path(row.source_video_path)
            except video.MediaPathError as exc:
                skip(row, f"source video: {exc}")
                continue
            dest_dir = video_abs.parent
        else:
            skip(row, "no source video — pick a target folder")
            continue

        out_name = _export_filename(row, version)
        dest_path = dest_dir / out_name

        if dest_path.exists():
            skip(row, "already exists", path=_relative_to_media(dest_path), name=out_name)
            continue

        try:
            shutil.copy2(src_path, dest_path)
        except OSError as exc:
            skip(row, str(exc), path=_relative_to_media(dest_path), name=out_name)
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
