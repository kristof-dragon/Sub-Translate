"""Single FIFO worker that drives files through detect → translate → write.

One worker by design — Ollama typically serves one request per model at a time,
so parallelism here just causes queueing downstream and makes progress reporting
jumpy. Files are taken from an asyncio.Queue in the order they were uploaded.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from . import models
from .db import SessionLocal
from .detector import detect_language
from .events import publish
from .languages import code_to_name
from .ollama_client import OllamaClient
from .subtitles import srt, vtt
from .translator import translate_cues

log = logging.getLogger("worker")

# Process-wide FIFO of File.id values waiting to be processed.
job_queue: asyncio.Queue[int] = asyncio.Queue()

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TRANSLATED_DIR = DATA_DIR / "translated"


def _set_status(file_id: int, **fields) -> None:
    with SessionLocal() as db:
        row = db.get(models.File, file_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def _load_context(file_id: int) -> tuple[models.File, models.Project, models.Settings] | None:
    with SessionLocal() as db:
        f = db.get(models.File, file_id)
        if f is None:
            return None
        proj = db.get(models.Project, f.project_id)
        settings = db.get(models.Settings, 1)
        # Detach by expunging so we can use the objects without the session open.
        db.expunge(f)
        if proj is not None:
            db.expunge(proj)
        if settings is not None:
            db.expunge(settings)
        return f, proj, settings  # type: ignore[return-value]


async def _process_file(file_id: int) -> None:
    ctx = _load_context(file_id)
    if ctx is None:
        return
    f, proj, settings = ctx

    ollama_url = settings.ollama_url
    api_key = settings.ollama_api_key or None
    chunk_size = int(settings.chunk_size or 30)
    model = f.model or (proj.default_model if proj else "") or settings.default_model
    target_lang = f.target_lang or (proj.default_target_lang if proj else "")

    if not ollama_url:
        raise RuntimeError("Ollama URL is not configured (Settings page)")
    if not model:
        raise RuntimeError("No model selected — set default model in Settings")
    if not target_lang:
        raise RuntimeError("No target language selected")

    # Read + parse source file.
    source_text = Path(f.stored_original_path).read_text(encoding="utf-8-sig", errors="replace")
    if f.format == "srt":
        cues = srt.parse_srt(source_text)
        writer = srt.write_srt
    elif f.format == "vtt":
        cues = vtt.parse_vtt(source_text)
        writer = vtt.write_vtt
    else:
        raise RuntimeError(f"Unsupported format: {f.format}")

    if not cues:
        raise RuntimeError("No cues found in file")

    async with OllamaClient(ollama_url, api_key) as client:
        # 1) Detect language.
        _set_status(file_id, status="detecting", progress_pct=0, error="")
        await publish({"file_id": file_id, "status": "detecting", "progress_pct": 0})

        sample = " ".join(c.text for c in cues[:20])
        detected = await detect_language(client, model, sample)
        _set_status(file_id, detected_lang=detected)
        await publish({"file_id": file_id, "status": "detecting", "detected_lang": detected, "progress_pct": 0})

        # 2) Translate.
        _set_status(file_id, status="translating", progress_pct=0)
        await publish({"file_id": file_id, "status": "translating", "progress_pct": 0, "detected_lang": detected})

        src_name = code_to_name(detected) or detected or "the source language"
        tgt_name = code_to_name(target_lang) or target_lang

        last_reported = -1

        async def on_progress(done: int, total: int) -> None:
            nonlocal last_reported
            pct = int(done * 100 / total) if total else 0
            # Only hit the DB / broadcast when the integer percent changes.
            if pct != last_reported:
                last_reported = pct
                _set_status(file_id, progress_pct=pct)
                await publish({"file_id": file_id, "status": "translating", "progress_pct": pct})

        translated_cues = await translate_cues(
            client,
            model=model,
            src_lang=src_name,
            tgt_lang=tgt_name,
            cues=cues,
            chunk_size=chunk_size,
            progress_cb=on_progress,
        )

    # 3) Write output file. Partition by project_id to keep per-project cleanup
    #    simple and mirror the uploads layout at /data/uploads/<pid>/.
    proj_out = TRANSLATED_DIR / str(f.project_id)
    proj_out.mkdir(parents=True, exist_ok=True)
    stem = Path(f.original_filename).stem
    out_path = proj_out / f"{file_id}_{stem}.{target_lang}.{f.format}"
    out_path.write_text(writer(translated_cues), encoding="utf-8")

    _set_status(
        file_id,
        stored_translated_path=str(out_path),
        status="done",
        progress_pct=100,
    )
    await publish({"file_id": file_id, "status": "done", "progress_pct": 100})


async def worker_loop() -> None:
    """Run forever: pull File ids off the queue and process them one at a time."""
    log.info("worker loop started")
    while True:
        file_id = await job_queue.get()
        try:
            await _process_file(file_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — we record every failure on the row
            log.exception("file %s failed", file_id)
            _set_status(file_id, status="error", error=str(exc))
            await publish({"file_id": file_id, "status": "error", "error": str(exc)})
        finally:
            job_queue.task_done()
