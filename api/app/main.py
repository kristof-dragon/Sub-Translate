"""FastAPI entrypoint — wires up routers, lifecycle, and the background worker."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .db import SessionLocal, init_db
from .routers import files, jobs, projects, settings, video
from .worker import extraction_worker_loop, worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Anything left mid-flight from a prior run is marked errored — both queues
    # are in-memory, so those jobs would otherwise hang forever in the UI.
    with SessionLocal() as db:
        stale = (
            db.query(models.File)
            .filter(
                models.File.status.in_(
                    ("extracting", "queued", "detecting", "translating")
                )
            )
            .all()
        )
        for row in stale:
            row.status = "error"
            row.error = "Interrupted by server restart"
        if stale:
            db.commit()
            log.info("marked %d stale files as errored on startup", len(stale))

    translate_task = asyncio.create_task(worker_loop(), name="translate-worker")
    extract_task = asyncio.create_task(extraction_worker_loop(), name="extract-worker")
    try:
        yield
    finally:
        for t in (translate_task, extract_task):
            t.cancel()
        for t in (translate_task, extract_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(title="Subtitle Translator API", version="1.3.0", lifespan=lifespan)

# Permissive CORS — the app is internal-only and served behind the web container's nginx.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(video.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}
