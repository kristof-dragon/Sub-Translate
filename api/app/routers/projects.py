"""Projects CRUD."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..db import SessionLocal

router = APIRouter(prefix="/projects", tags=["projects"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = ""
    default_target_lang: Optional[str] = ""
    default_model: Optional[str] = ""


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_target_lang: Optional[str] = None
    default_model: Optional[str] = None


def _serialize(p: models.Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "default_target_lang": p.default_target_lang,
        "default_model": p.default_model,
        "created_at": p.created_at.isoformat(),
        "file_count": len(p.files),
    }


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    rows = db.query(models.Project).order_by(models.Project.created_at.desc()).all()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_project(data: ProjectIn, db: Session = Depends(get_db)):
    p = models.Project(
        name=data.name.strip(),
        description=data.description or "",
        default_target_lang=data.default_target_lang or "",
        default_model=data.default_model or "",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return _serialize(p)


@router.patch("/{project_id}")
def update_project(project_id: int, data: ProjectPatch, db: Session = Depends(get_db)):
    p = db.get(models.Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    db.delete(p)
    db.commit()
