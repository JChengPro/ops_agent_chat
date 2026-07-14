from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_environment, require_environment_permission, require_project, require_project_permission
from app.context.collectors.manual import collect_manual_services
from app.context.collectors.registry import collectors_for
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.context import CollectorRun
from app.models.project import Connection, Environment, Project, ProjectMember
from app.models.user import User
from app.runtime.transports.ssh import SSHTransport
from app.utils.public_config import public_config

router = APIRouter(tags=["projects"])


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    is_pinned: bool = False


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    is_pinned: bool | None = None


class EnvironmentPayload(BaseModel):
    name: str = "default"
    runtime_type: Literal["manual", "docker_compose", "kubernetes", "systemd", "mixed"] = "manual"
    connection_id: int | None = None
    workdir: str | None = None
    namespace: str | None = None
    config_json: dict[str, Any] = Field(default_factory=dict)
    policy_profile: Literal["development", "test", "staging", "production"] = "development"
    is_default: bool = False


class EnvironmentPatch(BaseModel):
    name: str | None = None
    runtime_type: Literal["manual", "docker_compose", "kubernetes", "systemd", "mixed"] | None = None
    connection_id: int | None = None
    workdir: str | None = None
    namespace: str | None = None
    config_json: dict[str, Any] | None = None
    policy_profile: Literal["development", "test", "staging", "production"] | None = None
    is_default: bool | None = None


def project_out(item: Project) -> dict:
    return {"id": item.id, "owner_id": item.owner_id, "name": item.name, "description": item.description, "settings_json": public_config(item.settings_json), "is_active": item.is_active, "is_pinned": item.is_pinned, "created_at": item.created_at}


def validate_connection_scope(db: Session, project: Project, connection_id: int | None) -> None:
    if connection_id is None:
        return
    connection = db.get(Connection, connection_id)
    if not connection or connection.owner_id != project.owner_id:
        raise HTTPException(400, "Connection must belong to the project owner")


def environment_out(item: Environment) -> dict:
    return {"id": item.id, "project_id": item.project_id, "name": item.name, "runtime_type": item.runtime_type, "connection_id": item.connection_id, "workdir": item.workdir, "namespace": item.namespace, "config_json": public_config(item.config_json), "policy_profile": item.policy_profile, "is_default": item.is_default, "is_active": item.is_active}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    membership_projects = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    rows = db.scalars(select(Project).where(Project.is_active.is_(True), or_(Project.owner_id == user.id, Project.id.in_(membership_projects))).order_by(Project.is_pinned.desc(), Project.updated_at.desc())).all()
    return [project_out(item) for item in rows]


@router.post("/projects")
def create_project(payload: ProjectPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = Project(owner_id=user.id, name=payload.name, description=payload.description, is_pinned=payload.is_pinned)
    db.add(row); db.flush(); db.add(ProjectMember(project_id=row.id, user_id=user.id, role="owner")); db.add(Environment(project_id=row.id, name="default", runtime_type="manual", policy_profile="development", is_default=True)); db.commit(); db.refresh(row)
    return project_out(row)


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return project_out(require_project(db, user, project_id))


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, payload: ProjectPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_project_permission(db, user, project_id, "project.manage")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return project_out(row)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_project_permission(db, user, project_id, "project.manage"); row.is_active = False; db.commit(); return project_out(row)


@router.get("/projects/{project_id}/environments")
def list_environments(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    return [environment_out(item) for item in db.scalars(select(Environment).where(Environment.project_id == project_id, Environment.is_active.is_(True)).order_by(Environment.is_default.desc(), Environment.name)).all()]


@router.post("/projects/{project_id}/environments")
def create_environment(project_id: int, payload: EnvironmentPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project_permission(db, user, project_id, "project.manage")
    validate_connection_scope(db, project, payload.connection_id)
    row = Environment(project_id=project_id, **payload.model_dump()); db.add(row); db.flush(); collect_manual_services(db, row); db.commit(); db.refresh(row); return environment_out(row)


@router.patch("/environments/{environment_id}")
def patch_environment(environment_id: int, payload: EnvironmentPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment_permission(db, user, environment_id, "project.manage")
    project = db.get(Project, row.project_id)
    if "connection_id" in payload.model_fields_set:
        validate_connection_scope(db, project, payload.connection_id)
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return environment_out(row)


@router.delete("/environments/{environment_id}")
def delete_environment(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment_permission(db, user, environment_id, "project.manage"); row.is_active = False; db.commit(); return environment_out(row)


@router.post("/environments/{environment_id}/test-connection")
def test_connection(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment(db, user, environment_id); connection = db.get(Connection, row.connection_id)
    if not connection: raise HTTPException(400, "Environment has no connection")
    ok, message = SSHTransport().test_connection(connection); connection.status = "connected" if ok else "failed"; db.commit(); return {"ok": ok, "message": message}


@router.post("/environments/{environment_id}/collect-context")
def collect_context(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment(db, user, environment_id); manual = collect_manual_services(db, row); runs = [manual]
    if row.connection_id:
        connection = db.get(Connection, row.connection_id)
        runs.extend(collector.collect(db, row, connection) for collector in collectors_for(row))
    db.commit(); return [{"id": item.id, "collector_name": item.collector_name, "status": item.status, "summary_json": item.summary_json, "error_message": item.error_message} for item in runs]


@router.get("/environments/{environment_id}/collector-runs")
def collector_runs(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_environment(db, user, environment_id)
    return db.execute(select(CollectorRun).where(CollectorRun.environment_id == environment_id).order_by(CollectorRun.created_at.desc())).scalars().all()
