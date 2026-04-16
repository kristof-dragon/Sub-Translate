"""MKV browsing, track listing, and subtitle extraction helpers.

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

# codec_id from mkvmerge -J output → (our file extension, do-we-support-translating-it)
#
# We translate text-based formats only. Bitmap formats (PGS/VobSub) would
# require OCR before translation, which is out of scope. ASS/SSA is text but
# our parsers don't handle style overrides yet, so we list them but disable.
CODEC_ID_MAP: dict[str, tuple[str, bool]] = {
    "S_TEXT/UTF8": ("srt", True),
    "S_TEXT/SRT": ("srt", True),
    "S_TEXT/WEBVTT": ("vtt", True),
    "S_TEXT/ASS": ("ass", False),
    "S_TEXT/SSA": ("ass", False),
    "S_VOBSUB": ("sub", False),
    "S_HDMV/PGS": ("sup", False),
    "S_HDMV/TEXTST": ("textst", False),
}


class MediaPathError(Exception):
    """Raised when a requested path cannot be served (missing, outside root, wrong type)."""


@dataclass
class BrowseEntry:
    name: str
    is_dir: bool
    is_mkv: bool
    size: int | None


@dataclass
class MkvTrack:
    id: int
    codec: str
    codec_id: str
    language: str
    name: str
    ext: str | None
    supported: bool


def _media_root() -> Path:
    """Return the resolved media root, ensuring the mount actually exists."""
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

    Returns only sub-directories (always) and `.mkv` files (case-insensitive).
    Other file types are hidden to keep the picker focused on the task at hand.
    """
    path = resolve_media_path(relative)
    if not path.is_dir():
        raise MediaPathError("Not a directory")

    root = _media_root()
    entries: list[BrowseEntry] = []
    for p in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if p.name.startswith("."):
            continue
        is_dir = p.is_dir()
        is_mkv = p.is_file() and p.suffix.lower() == ".mkv"
        if not is_dir and not is_mkv:
            continue
        entries.append(
            BrowseEntry(
                name=p.name,
                is_dir=is_dir,
                is_mkv=is_mkv,
                size=p.stat().st_size if p.is_file() else None,
            )
        )

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


def list_mkv_tracks(relative: str) -> list[MkvTrack]:
    """Return the subtitle tracks found inside an MKV at `relative`.

    Runs `mkvmerge -J` and filters to `type == "subtitles"`. Each track is
    annotated with whether we can translate it (based on codec_id).
    """
    path = resolve_media_path(relative)
    if path.suffix.lower() != ".mkv" or not path.is_file():
        raise MediaPathError("Not an MKV file")

    try:
        proc = subprocess.run(
            ["mkvmerge", "-J", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise MediaPathError("mkvmerge is not installed in the api container") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaPathError(f"mkvmerge failed: {exc.stderr.strip() or exc}") from exc

    data = json.loads(proc.stdout)
    return [_track_from_json(t) for t in data.get("tracks", []) if t.get("type") == "subtitles"]


def _track_from_json(t: dict) -> MkvTrack:
    props = t.get("properties", {}) or {}
    codec_id = props.get("codec_id") or t.get("codec", "")
    ext, supported = CODEC_ID_MAP.get(codec_id, (None, False))
    return MkvTrack(
        id=int(t.get("id", 0)),
        codec=str(t.get("codec", "")),
        codec_id=str(codec_id),
        language=str(props.get("language_ietf") or props.get("language") or ""),
        name=str(props.get("track_name") or ""),
        ext=ext,
        supported=supported,
    )


def extract_track(relative: str, track_id: int, out_path: Path) -> None:
    """Extract one track to `out_path` with mkvextract (lossless demux)."""
    mkv_path = resolve_media_path(relative)
    if mkv_path.suffix.lower() != ".mkv" or not mkv_path.is_file():
        raise MediaPathError("Not an MKV file")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "mkvextract",
                "tracks",
                str(mkv_path),
                f"{int(track_id)}:{out_path}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=1800,
        )
    except FileNotFoundError as exc:
        raise MediaPathError("mkvextract is not installed in the api container") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaPathError(f"mkvextract failed: {exc.stderr.strip() or exc}") from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise MediaPathError("mkvextract produced no output")
