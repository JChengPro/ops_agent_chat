from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_project, require_project_permission
from app.context.service import entity_to_dict
from app.core.database import get_db
from app.core.security import get_current_user
from app.experience.service import index_experience, search_experience
from app.models.context import ProjectEntity, ProjectRelationship
from app.models.experience import ExperienceChunk, ExperienceItem
from app.models.user import User

router = APIRouter(tags=["context", "experience"])


class ExperiencePayload(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=500000)
    item_type: str = "project_note"
    tags: list[str] = Field(default_factory=list, max_length=30)
    applicable_entities: list[str] = Field(default_factory=list, max_length=50)
    trust_status: str = "draft"


class ExperiencePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=500000)
    tags: list[str] | None = None
    applicable_entities: list[str] | None = None
    trust_status: str | None = None


class SearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


def experience_out(item: ExperienceItem) -> dict:
    return {"id": item.id, "project_id": item.project_id, "environment_id": item.environment_id, "title": item.title, "item_type": item.item_type, "content": item.content, "tags": item.tags, "applicable_entities": item.applicable_entities, "source_type": item.source_type, "source_ref": item.source_ref, "trust_status": item.trust_status, "verified_at": item.verified_at, "created_at": item.created_at, "updated_at": item.updated_at}


@router.get("/projects/{project_id}/entities")
def entities(project_id: int, environment_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    statement = select(ProjectEntity).where(ProjectEntity.project_id == project_id, ProjectEntity.is_active.is_(True))
    if environment_id: statement = statement.where(ProjectEntity.environment_id == environment_id)
    return [entity_to_dict(item) for item in db.scalars(statement.order_by(ProjectEntity.entity_type, ProjectEntity.canonical_name)).all()]


@router.get("/projects/{project_id}/relationships")
def relationships(project_id: int, environment_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    statement = select(ProjectRelationship).where(ProjectRelationship.project_id == project_id, ProjectRelationship.is_active.is_(True))
    if environment_id: statement = statement.where(ProjectRelationship.environment_id == environment_id)
    return db.scalars(statement.order_by(ProjectRelationship.relation_type)).all()


@router.get("/projects/{project_id}/experience")
def experience(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    return [experience_out(item) for item in db.scalars(select(ExperienceItem).where(ExperienceItem.project_id == project_id).order_by(ExperienceItem.updated_at.desc())).all()]


@router.post("/projects/{project_id}/experience")
def create_experience(project_id: int, payload: ExperiencePayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project_permission(db, user, project_id, "project.manage")
    verified = payload.trust_status == "verified"
    row = ExperienceItem(project_id=project_id, title=payload.title, content=payload.content, item_type=payload.item_type, tags=payload.tags, applicable_entities=payload.applicable_entities, source_type="manual", trust_status=payload.trust_status, created_by=user.id, verified_by=user.id if verified else None, verified_at=datetime.now(timezone.utc) if verified else None)
    db.add(row); db.flush(); index_experience(db, row); db.commit(); db.refresh(row); return experience_out(row)


@router.patch("/experience/{item_id}")
def patch_experience(item_id: int, payload: ExperiencePatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ExperienceItem, item_id)
    if not row: raise HTTPException(404, "Experience item not found")
    require_project_permission(db, user, row.project_id, "project.manage")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    if row.trust_status == "verified": row.verified_by = user.id; row.verified_at = datetime.now(timezone.utc)
    else: row.verified_by = None; row.verified_at = None
    db.flush(); index_experience(db, row); db.commit(); db.refresh(row); return experience_out(row)


@router.delete("/experience/{item_id}")
def delete_experience(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ExperienceItem, item_id)
    if not row: raise HTTPException(404, "Experience item not found")
    require_project_permission(db, user, row.project_id, "project.manage"); db.delete(row); db.commit(); return {"deleted": True, "id": item_id}


@router.post("/projects/{project_id}/experience/search")
def search(project_id: int, payload: SearchPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id); return search_experience(db, project_id, payload.query, payload.limit)
