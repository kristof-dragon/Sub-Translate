"""Settings singleton + Ollama discovery + curated language list."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..db import SessionLocal
from ..languages import LANGUAGES
from ..ollama_client import OllamaClient

router = APIRouter(prefix="/settings", tags=["settings"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SettingsPatch(BaseModel):
    ollama_url: Optional[str] = None
    ollama_api_key: Optional[str] = None  # empty string clears it
    default_model: Optional[str] = None
    chunk_size: Optional[int] = Field(default=None, ge=1, le=500)


def _serialize(s: models.Settings) -> dict:
    return {
        "ollama_url": s.ollama_url,
        "ollama_api_key_set": bool(s.ollama_api_key),
        "default_model": s.default_model,
        "chunk_size": s.chunk_size,
    }


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    s = db.get(models.Settings, 1)
    if not s:
        raise HTTPException(500, "Settings row missing")
    return _serialize(s)


@router.put("")
def update_settings(data: SettingsPatch, db: Session = Depends(get_db)):
    s = db.get(models.Settings, 1)
    if not s:
        raise HTTPException(500, "Settings row missing")
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        if v is None:
            continue
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _serialize(s)


@router.get("/models")
async def list_ollama_models(db: Session = Depends(get_db)):
    s = db.get(models.Settings, 1)
    if not s or not s.ollama_url:
        raise HTTPException(400, "Ollama URL not configured")
    try:
        async with OllamaClient(s.ollama_url, s.ollama_api_key or None, timeout=15.0) as client:
            models_list = await client.list_models()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Ollama error: {exc}") from exc
    return {
        "models": [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
            }
            for m in models_list
        ]
    }


@router.get("/languages")
def list_languages():
    return [{"code": code, "name": name} for code, name in LANGUAGES]
