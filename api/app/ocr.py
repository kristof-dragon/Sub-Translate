"""OCR for bitmap subtitle formats.

Two backends are wired in:
  - PGS (Blu-ray `.sup`) → pgsrip (Python wrapper around tesseract).
  - VobSub (DVD `.sub` + `.idx` pair) → subtile-ocr (Rust CLI, bundled
    into the API image, also drives tesseract underneath).

Both produce an SRT that we parse back into the project's `Cue` type so
the rest of the pipeline doesn't care which backend ran. DVB stays
deferred — no maintained tooling.

An optional text-only LLM cleanup pass runs each cue through Ollama with
a "fix OCR errors" prompt before the row is offered for translation.
Vision-LLM cleanup (sending the rendered subtitle PNG alongside the
candidate text) is on the roadmap but needs a per-cue render pipeline
that neither backend's high-level API exposes.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from .ollama_client import OllamaClient
from .subtitles.cue import Cue
from .subtitles.srt import parse_srt

log = logging.getLogger("ocr")

ProgressCB = Callable[[int, int], Awaitable[None]]


# Mapping from ISO-639-1 (two-letter, what the UI / ffprobe usually carry)
# to the three-letter code tesseract / pgsrip expect for traineddata names.
# Only languages we ship traineddata for (see api/Dockerfile) are listed —
# anything else falls back to English so OCR still produces *something*.
_LANG_2_TO_3: dict[str, str] = {
    "en": "eng",
    "hu": "hun",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "ja": "jpn",
    "ko": "kor",
    "zh": "chi_sim",
    "ar": "ara",
}

_BUNDLED_3: set[str] = {
    "eng", "hun", "spa", "fra", "deu", "ita", "por", "rus",
    "jpn", "kor", "chi_sim", "chi_tra", "ara",
}


def normalize_lang_for_tesseract(lang_hint: str | None) -> str:
    """Map an arbitrary language tag to a bundled tesseract code.

    Accepts ISO-639-1 ("en") or ISO-639-2 ("eng") hints. Falls back to
    "eng" for anything we don't have traineddata for so OCR can still
    produce best-effort output rather than failing outright.
    """
    if not lang_hint:
        return "eng"
    h = lang_hint.strip().lower().replace("-", "_")
    if len(h) == 3 and h in _BUNDLED_3:
        return h
    if len(h) == 2 and h in _LANG_2_TO_3:
        return _LANG_2_TO_3[h]
    # Locale-style "en_US" → take the language part.
    if "_" in h:
        prefix = h.split("_", 1)[0]
        if len(prefix) == 2 and prefix in _LANG_2_TO_3:
            return _LANG_2_TO_3[prefix]
    return "eng"


def ocr_pgs_sup(sup_path: Path, lang_hint: str | None) -> list[Cue]:
    """OCR a PGS .sup file via pgsrip; return the resulting cues.

    pgsrip's `Sup` class parses the language out of the FILENAME, so we
    stage the input under a name like `input.eng.sup` in a temp dir
    rather than mutating the caller's file. pgsrip writes its SRT next
    to the input on success; we read it back and return parsed cues.
    """
    # Imported lazily so the module is importable in test environments
    # that don't have pgsrip + tesseract available.
    from babelfish import Language
    from pgsrip import Options, Sup, pgsrip

    lang3 = normalize_lang_for_tesseract(lang_hint)

    work_dir = Path(tempfile.mkdtemp(prefix="ocr_pgs_"))
    try:
        staged = work_dir / f"input.{lang3}.sup"
        shutil.copy2(sup_path, staged)

        media = Sup(str(staged))
        options = Options(
            languages={Language(lang3)},
            overwrite=True,
            one_per_lang=False,
        )
        pgsrip.rip(media, options)

        # pgsrip writes the SRT alongside the input. Filename pattern can
        # vary across versions (input.eng.srt vs input.srt), so scan for
        # any *.srt the run produced rather than assuming one shape.
        srt_files = sorted(work_dir.glob("*.srt"))
        if not srt_files:
            raise RuntimeError(
                "pgsrip produced no .srt output (check tesseract is installed "
                "and the language pack for "
                f"{lang3!r} is present)"
            )
        srt_text = srt_files[0].read_text(encoding="utf-8-sig", errors="replace")
        cues = parse_srt(srt_text)
        if not cues:
            raise RuntimeError("pgsrip emitted an empty SRT (no cues recognised)")
        return cues
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def ocr_vobsub(sub_path: Path, lang_hint: str | None) -> list[Cue]:
    """OCR a VobSub `.sub` via subtile-ocr; return parsed cues.

    Expects a sibling `.idx` next to `sub_path` — ffmpeg writes the pair
    automatically when extracting a `dvd_subtitle` stream. subtile-ocr is
    a standalone Rust CLI bundled into the API image (multi-stage Docker
    build); we shell out and read the SRT it produces back through our
    existing parser. Falls back to English when no language hint is
    available; tesseract still produces best-effort text either way.
    """
    idx_path = sub_path.with_suffix(".idx")
    if not idx_path.is_file():
        raise RuntimeError(
            f"VobSub .idx sidecar missing — expected {idx_path.name} next to "
            f"{sub_path.name}; ffmpeg writes both files during extraction"
        )

    lang3 = normalize_lang_for_tesseract(lang_hint)

    work_dir = Path(tempfile.mkdtemp(prefix="ocr_vobsub_"))
    try:
        out_srt = work_dir / "out.srt"
        try:
            subprocess.run(
                [
                    "subtile-ocr",
                    "-l", lang3,
                    "-o", str(out_srt),
                    str(idx_path),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=1800,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "subtile-ocr is not installed in the api container"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"subtile-ocr failed: {(exc.stderr or '').strip() or exc}"
            ) from exc

        if not out_srt.exists() or out_srt.stat().st_size == 0:
            raise RuntimeError("subtile-ocr produced no .srt output")

        srt_text = out_srt.read_text(encoding="utf-8-sig", errors="replace")
        cues = parse_srt(srt_text)
        if not cues:
            raise RuntimeError("subtile-ocr emitted an empty SRT (no cues recognised)")
        return cues
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


_CLEANUP_PROMPT = (
    "You are correcting OCR errors in a subtitle line. The text was OCR'd "
    "from a bitmap subtitle (Blu-ray PGS or DVD VobSub) in {lang}. Fix "
    "obvious OCR mistakes: letter/digit confusions (l/I/1, 0/O), missing "
    "or wrong diacritics, broken word boundaries, stray punctuation. "
    "PRESERVE: original meaning, line breaks (the literal token <BR>), and "
    "HTML-like style tags (<i>, <b>, <u>, <font>). Do NOT translate. Do "
    "NOT add commentary. Return ONLY the corrected line.\n\n"
    "OCR'd line:\n{text}"
)
_BR = "<BR>"


def _encode_cue_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", _BR)


def _decode_cue_text(text: str) -> str:
    return text.replace(_BR, "\n").strip()


async def llm_cleanup_cues(
    client: OllamaClient,
    model: str,
    cues: list[Cue],
    lang_hint: str | None,
    progress_cb: ProgressCB | None = None,
) -> list[Cue]:
    """Per-cue LLM-driven OCR error correction.

    One Ollama call per cue — kept narrow on purpose so the model has a
    single short fragment to consider, which keeps latency predictable
    and hallucination risk low. If a cue's correction comes back empty
    or malformed, the original text is preserved so we never make a row
    worse than tesseract left it.
    """
    if not cues:
        return cues
    lang_label = (lang_hint or "the source language").strip() or "the source language"
    total = len(cues)
    cleaned: list[Cue] = []
    for done, c in enumerate(cues, start=1):
        encoded = _encode_cue_text(c.text)
        prompt = _CLEANUP_PROMPT.format(lang=lang_label, text=encoded)
        try:
            raw = await client.generate(model=model, prompt=prompt)
        except Exception as exc:  # noqa: BLE001 — log + keep the original line
            log.warning("OCR cleanup failed for cue %s: %s", c.index, exc)
            cleaned.append(c)
            if progress_cb is not None:
                await progress_cb(done, total)
            continue
        new_text = _decode_cue_text(raw or "")
        # Defensive: model returned nothing useful → keep original.
        if not new_text:
            cleaned.append(c)
        else:
            cleaned.append(Cue(index=c.index, start=c.start, end=c.end, text=new_text))
        if progress_cb is not None:
            await progress_cb(done, total)
    return cleaned


def lang_hint_from_filename(filename: str) -> str | None:
    """Best-effort language hint from an extraction filename.

    Extracted bitmap subtitles are named `<stem>.<lang>.stream<n>.<ext>`
    by routers/video.py (same convention for `.sup` PGS and `.sub`
    VobSub), so the language tag is the second-from-last dot-separated
    chunk (when present and 2-3 chars). Returns None if no plausible
    tag is found, which `normalize_lang_for_tesseract` will then
    resolve to English.
    """
    parts = Path(filename).name.split(".")
    # Need at least <stem>.<lang>.<something>.<ext> → 4 parts.
    if len(parts) < 4:
        return None
    candidate = parts[-3].lower()
    if 2 <= len(candidate) <= 3 and candidate.isalpha():
        return candidate
    return None
