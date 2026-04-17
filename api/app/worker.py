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

from . import models, ocr, video
from .db import SessionLocal
from .detector import detect_language
from .events import publish
from .languages import code_to_name
from .ollama_client import OllamaClient
from .subtitles import srt, vtt
from .translator import translate_cues

log = logging.getLogger("worker")

# Process-wide FIFO of File.id values waiting to be translated.
job_queue: asyncio.Queue[int] = asyncio.Queue()

# Separate FIFO for ffmpeg extractions. Parallel with `job_queue` because ffmpeg
# and Ollama don't contend for the same resources — an in-flight translation
# shouldn't stall "drop another video into the queue" from the picker modal.
# Each item is (file_id, video_relpath, ffmpeg_stream_index).
extract_queue: asyncio.Queue[tuple[int, str, int]] = asyncio.Queue()

# Third FIFO for PGS OCR jobs. CPU-bound on tesseract; running it in its own
# queue keeps it from blocking ffmpeg (I/O-bound) or translation (Ollama-bound).
ocr_queue: asyncio.Queue[int] = asyncio.Queue()

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
TRANSLATED_DIR = DATA_DIR / "translated"
OCR_DIR = DATA_DIR / "ocr"


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
    timeout = float(settings.request_timeout or 600)
    # Only forward think=False when the operator explicitly toggled it; leaving
    # it None lets Ollama fall back to the model's own default.
    think = False if getattr(settings, "disable_thinking", 0) else None
    num_ctx = int(getattr(settings, "num_ctx", 0) or 0) or None
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

    async with OllamaClient(
        ollama_url,
        api_key,
        timeout=timeout,
        think=think,
        num_ctx=num_ctx,
    ) as client:
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


async def _process_extraction(file_id: int, video_path: str, track_id: int) -> None:
    """Run ffmpeg for one already-created File row and flip its status.

    For text formats the row lands at `extracted` and waits for the operator
    to click Translate. For bitmap formats (currently PGS) the row lands at
    `ocr_queued` and is auto-enqueued onto `ocr_queue` — the operator gets
    a chance to review the OCR output before triggering translation.
    """
    with SessionLocal() as db:
        row = db.get(models.File, file_id)
        if row is None:
            return
        proj_id = row.project_id
        filename = row.original_filename
        source_format = row.source_format or ""

    out_path = UPLOAD_DIR / str(proj_id) / f"{file_id}_{filename}"
    # Blocking ffmpeg — run in a thread so the asyncio loop keeps serving SSE etc.
    await asyncio.to_thread(video.extract_track, video_path, track_id, out_path)

    if source_format == "pgs":
        _set_status(
            file_id,
            stored_original_path=str(out_path),
            status="ocr_queued",
            progress_pct=0,
            ocr_progress_pct=0,
            error="",
        )
        await publish({"file_id": file_id, "status": "ocr_queued", "progress_pct": 0, "ocr_progress_pct": 0})
        await ocr_queue.put(file_id)
        return

    # Persist disk path + promote to "extracted" only after ffmpeg produced output.
    _set_status(
        file_id,
        stored_original_path=str(out_path),
        status="extracted",
        progress_pct=100,
        error="",
    )
    await publish({"file_id": file_id, "status": "extracted", "progress_pct": 100})


async def extraction_worker_loop() -> None:
    """Run forever: pull extraction jobs and demux them one at a time with ffmpeg."""
    log.info("extraction worker loop started")
    while True:
        file_id, video_path, track_id = await extract_queue.get()
        try:
            await _process_extraction(file_id, video_path, track_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("extraction for file %s failed", file_id)
            _set_status(file_id, status="error", error=str(exc))
            await publish({"file_id": file_id, "status": "error", "error": str(exc)})
        finally:
            extract_queue.task_done()


async def _process_ocr(file_id: int) -> None:
    """OCR a bitmap-source file and (optionally) clean up via Ollama.

    Pipeline:
      1. Run pgsrip on the .sup → list[Cue] (in a thread; pgsrip is sync).
      2. If the operator enabled OCR cleanup in Settings, send each cue's
         text through Ollama for OCR-error correction.
      3. Write the cues to /data/ocr/<pid>/<id>_<stem>.srt.
      4. Repoint stored_original_path at the SRT, flip format to "srt",
         and land the row at status="ocr_done" (NOT auto-queued — operator
         clicks Translate to start the translation pass, matching the
         existing extracted → manual-Translate flow).
    """
    ctx = _load_context(file_id)
    if ctx is None:
        return
    f, _proj, settings = ctx

    if (f.source_format or "") != "pgs":
        # Defensive: only PGS is implemented in v1.4.0. Any other bitmap
        # source shouldn't have been enqueued, but if it was, fail loud
        # rather than silently dropping the row.
        raise RuntimeError(f"OCR not supported for source_format={f.source_format!r}")

    sup_path = Path(f.stored_original_path)
    if not sup_path.is_file():
        raise RuntimeError(f"PGS file missing on disk: {sup_path}")

    # OCR phase 1: pgsrip → cues. The language hint comes from the original
    # filename (extraction writes `<stem>.<lang>.streamN.sup`), and ocr.py
    # falls back to English if no plausible tag is present.
    lang_hint = ocr.lang_hint_from_filename(f.original_filename)

    _set_status(file_id, status="ocr_running", ocr_progress_pct=0, error="")
    await publish({"file_id": file_id, "status": "ocr_running", "ocr_progress_pct": 0})

    cues = await asyncio.to_thread(ocr.ocr_pgs_sup, sup_path, lang_hint)

    # pgsrip is a single shell-out — there's no fine-grained progress for
    # the OCR call itself. Mark phase 1 done at 50% so the cleanup phase
    # has visible progress to fill in (or jump straight to 100% when off).
    cleanup_on = bool(getattr(settings, "ocr_llm_cleanup", 0)) if settings else False
    if cleanup_on:
        _set_status(file_id, ocr_progress_pct=50)
        await publish({"file_id": file_id, "status": "ocr_running", "ocr_progress_pct": 50})

        cleanup_model = (
            (getattr(settings, "ocr_llm_model", "") or "").strip()
            or (settings.default_model or "").strip()
        )
        if not cleanup_model:
            log.warning(
                "OCR cleanup enabled for file %s but no model is configured "
                "(ocr_llm_model and default_model both empty); skipping cleanup",
                file_id,
            )
        elif not settings.ollama_url:
            log.warning(
                "OCR cleanup enabled for file %s but Ollama URL is not "
                "configured; skipping cleanup",
                file_id,
            )
        else:
            timeout = float(settings.request_timeout or 600)
            think = False if getattr(settings, "disable_thinking", 0) else None
            num_ctx = int(getattr(settings, "num_ctx", 0) or 0) or None
            api_key = settings.ollama_api_key or None

            async def on_cleanup_progress(done: int, total: int) -> None:
                # Cleanup occupies 50–100% of the OCR phase progress bar.
                pct = 50 + int(done * 50 / total) if total else 50
                _set_status(file_id, ocr_progress_pct=pct)
                await publish({"file_id": file_id, "status": "ocr_running", "ocr_progress_pct": pct})

            async with OllamaClient(
                settings.ollama_url,
                api_key,
                timeout=timeout,
                think=think,
                num_ctx=num_ctx,
            ) as client:
                cues = await ocr.llm_cleanup_cues(
                    client,
                    cleanup_model,
                    cues,
                    lang_hint,
                    progress_cb=on_cleanup_progress,
                )

    # Write the cues out as SRT under /data/ocr/<pid>/. The on-disk filename
    # follows the same `{file_id}_{stem}` convention as uploads/translated
    # so per-project cleanup stays simple.
    proj_ocr = OCR_DIR / str(f.project_id)
    proj_ocr.mkdir(parents=True, exist_ok=True)
    stem = Path(f.original_filename).stem
    ocr_path = proj_ocr / f"{file_id}_{stem}.srt"
    ocr_path.write_text(srt.write_srt(cues), encoding="utf-8")

    # Repoint the row at the freshly-written SRT and flip `format` so the
    # translate worker can pick it up unchanged. `source_format` stays "pgs"
    # so the UI can label the row as OCR-origin.
    _set_status(
        file_id,
        stored_original_path=str(ocr_path),
        format="srt",
        status="ocr_done",
        ocr_progress_pct=100,
        progress_pct=0,
        error="",
    )
    await publish({
        "file_id": file_id,
        "status": "ocr_done",
        "ocr_progress_pct": 100,
        "format": "srt",
    })


async def ocr_worker_loop() -> None:
    """Run forever: pull OCR jobs and OCR them one at a time."""
    log.info("ocr worker loop started")
    while True:
        file_id = await ocr_queue.get()
        try:
            await _process_ocr(file_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("ocr for file %s failed", file_id)
            _set_status(file_id, status="ocr_error", error=str(exc))
            await publish({"file_id": file_id, "status": "ocr_error", "error": str(exc)})
        finally:
            ocr_queue.task_done()
