"""Video (MKV / MP4 / WebM / etc.) browsing + subtitle track discovery + extraction.

Uses ffprobe for JSON stream enumeration and ffmpeg for extraction. Compared
with the previous mkvtoolnix-based implementation this covers more container
formats (mp4, webm, mov, avi, ts, ...) and lets us transmux MP4's `mov_text`
into SRT via `-c:s srt` — mkvtoolnix couldn't touch MP4 at all.

All filesystem access is constrained to `MEDIA_DIR` via `resolve_media_path` —
the bind-mounted folder is the only thing the API is permitted to read. Any
user-supplied path that resolves outside that root is rejected, so `../` tricks
cannot escape to arbitrary host files.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))

# Container extensions the browser will surface. Case-insensitive match.
VIDEO_EXTS: set[str] = {
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm",
    ".ts", ".mpg", ".mpeg", ".flv",
}

# ffprobe `codec_name` → (output extension, is-translatable-text, ffmpeg `-c:s` arg).
#
# Text formats are translated directly. Bitmap formats (PGS, VobSub) route
# through an OCR stage (see app/ocr.py) before translation. ASS/SSA is text
# but our SRT/VTT parsers don't handle its style overrides yet. DVB stays
# bitmap-but-unsupported — no maintained Python or CLI decoder we can ship.
# `mov_text` is MP4's internal text format — ffmpeg transmuxes to SRT.
#
# For VobSub: ffmpeg's vobsub muxer writes BOTH a `.sub` (subpicture stream)
# and an `.idx` (text index of timings/positions) when the output extension
# is `.sub` — the OCR stage uses both via subtile-ocr.
CODEC_MAP: dict[str, tuple[str, bool, str]] = {
    "subrip":            ("srt", True,  "copy"),
    "srt":               ("srt", True,  "copy"),   # alias some builds emit
    "webvtt":            ("vtt", True,  "copy"),
    "mov_text":          ("srt", True,  "srt"),    # MP4 tx3g → SRT
    "ass":               ("ass", False, "copy"),
    "ssa":               ("ass", False, "copy"),
    "hdmv_pgs_subtitle": ("sup", True,  "copy"),   # PGS → OCR'd to SRT (pgsrip)
    "dvd_subtitle":      ("sub", True,  "copy"),   # VobSub → OCR'd (subtile-ocr)
    "dvb_subtitle":      ("sub", False, "copy"),
}

# Codecs whose extracted output must be OCR'd before it can be translated.
# Used by the extraction worker to decide whether to enqueue the file onto
# the OCR worker (instead of marking it `extracted` and waiting for the
# operator's manual Translate click).
BITMAP_CODECS: set[str] = {"hdmv_pgs_subtitle", "dvd_subtitle"}


def source_format_for(codec: str) -> str:
    """Return the `File.source_format` value to record for a freshly-extracted
    track of `codec`. Empty string for text formats — `source_format` is only
    populated when OCR is required."""
    if codec == "hdmv_pgs_subtitle":
        return "pgs"
    if codec == "dvd_subtitle":
        return "vobsub"
    return ""


class MediaPathError(Exception):
    """Raised when a requested path cannot be served (missing, outside root, wrong type)."""


@dataclass
class BrowseEntry:
    name: str
    is_dir: bool
    is_video: bool
    size: int | None


@dataclass
class VideoTrack:
    """One subtitle stream inside a container.

    `id` is the ABSOLUTE ffmpeg stream index (e.g. `0:3`), so it can be passed
    straight to `ffmpeg -map 0:<id>` without any re-indexing.
    """

    id: int
    codec: str
    codec_id: str  # same value as `codec`; kept for frontend compatibility
    language: str
    name: str
    ext: str | None
    supported: bool


def _media_root() -> Path:
    if not MEDIA_DIR.exists():
        raise MediaPathError(
            f"Media directory {MEDIA_DIR} is not mounted — set MEDIA_PATH in .env"
        )
    return MEDIA_DIR.resolve()


def resolve_media_path(relative: str) -> Path:
    """Resolve `relative` under MEDIA_DIR, refusing any escape from the root."""
    root = _media_root()
    rel = (relative or "").lstrip("/").strip()
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise MediaPathError("Path escapes media root") from exc
    if not target.exists():
        raise MediaPathError("Path not found")
    return target


def browse(relative: str = "") -> dict:
    """List a directory under MEDIA_DIR.

    Returns only sub-directories (always) and video files with a whitelisted
    extension. Other files (e.g. `.txt`, `.nfo`) are hidden to keep the picker
    focused on the task at hand.
    """
    path = resolve_media_path(relative)
    if not path.is_dir():
        raise MediaPathError("Not a directory")

    root = _media_root()
    entries: list[BrowseEntry] = []
    # Skip dotfiles BEFORE any stat() call — macOS AppleDouble sidecars like
    # `._.DS_Store` can raise PermissionError from inside a container running
    # as a non-root UID, and we don't want to surface them anyway.
    visible = [p for p in path.iterdir() if not p.name.startswith(".")]
    for p in sorted(visible, key=lambda x: x.name.lower()):
        try:
            is_dir = p.is_dir()
            if is_dir:
                is_video = False
                size = None
            else:
                is_video = p.is_file() and p.suffix.lower() in VIDEO_EXTS
                size = p.stat().st_size
        except OSError:
            # Broken symlink, perm-denied sidecar, ... — just hide it.
            continue
        if not is_dir and not is_video:
            continue
        entries.append(
            BrowseEntry(
                name=p.name,
                is_dir=is_dir,
                is_video=is_video,
                size=size,
            )
        )
    # Sort dirs before files; names within each group are already alphabetical.
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    rel_str = "" if path == root else str(path.relative_to(root))
    parent_str: str | None
    if path == root:
        parent_str = None
    else:
        parent = path.parent
        parent_str = "" if parent == root else str(parent.relative_to(root))

    return {
        "path": rel_str,
        "parent": parent_str,
        "entries": [e.__dict__ for e in entries],
    }


def list_video_tracks(relative: str) -> list[VideoTrack]:
    """Return the subtitle tracks found inside a video container at `relative`.

    Runs `ffprobe -select_streams s` and maps each stream through CODEC_MAP.
    """
    path = resolve_media_path(relative)
    if not path.is_file():
        raise MediaPathError("Not a file")
    if path.suffix.lower() not in VIDEO_EXTS:
        raise MediaPathError(f"Unsupported video extension: {path.suffix}")

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "s",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise MediaPathError("ffprobe is not installed in the api container") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaPathError(f"ffprobe failed: {exc.stderr.strip() or exc}") from exc

    data = json.loads(proc.stdout or "{}")
    return [_track_from_json(s) for s in data.get("streams", [])]


def _track_from_json(s: dict) -> VideoTrack:
    codec = str(s.get("codec_name") or "").lower()
    ext, supported, _ = CODEC_MAP.get(codec, (None, False, "copy"))
    tags = s.get("tags", {}) or {}
    return VideoTrack(
        id=int(s.get("index", 0)),
        codec=codec,
        codec_id=codec,
        language=str(tags.get("language") or ""),
        name=str(tags.get("title") or ""),
        ext=ext,
        supported=supported,
    )


def extract_track(relative: str, track_index: int, out_path: Path) -> None:
    """Extract one subtitle stream to `out_path` using ffmpeg.

    Picks the right `-c:s` flag from CODEC_MAP: `copy` for already-text formats
    (srt/webvtt/ass/pgs/...), `srt` for MP4's mov_text to produce a clean .srt.
    """
    vid_path = resolve_media_path(relative)
    if not vid_path.is_file():
        raise MediaPathError("Not a file")
    if vid_path.suffix.lower() not in VIDEO_EXTS:
        raise MediaPathError(f"Unsupported video extension: {vid_path.suffix}")

    # Re-probe so we can look up the right codec arg for this specific stream,
    # and reject unsupported codecs before spawning ffmpeg.
    tracks = list_video_tracks(relative)
    track = next((t for t in tracks if t.id == track_index), None)
    if track is None:
        raise MediaPathError(f"Track {track_index} not found")
    if not track.supported:
        raise MediaPathError(
            f"Track {track_index} codec {track.codec!r} is not a translatable text format"
        )
    codec_arg = CODEC_MAP[track.codec][2]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", str(vid_path),
                "-map", f"0:{int(track_index)}",
                "-c:s", codec_arg,
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=1800,
        )
    except FileNotFoundError as exc:
        raise MediaPathError("ffmpeg is not installed in the api container") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaPathError(f"ffmpeg failed: {exc.stderr.strip() or exc}") from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise MediaPathError("ffmpeg produced no output")

    # VobSub is a pair: ffmpeg writes the bitmap stream to `<name>.sub` AND
    # the timing/position index to `<name>.idx`. The OCR step needs both —
    # surface a missing .idx as an extraction failure so the operator sees
    # the problem here instead of as a confusing OCR-time error.
    if track.codec == "dvd_subtitle":
        idx_path = out_path.with_suffix(".idx")
        if not idx_path.exists() or idx_path.stat().st_size == 0:
            raise MediaPathError(
                "ffmpeg did not produce the .idx sidecar — VobSub OCR needs both "
                ".sub and .idx and ffmpeg should have written them together"
            )
