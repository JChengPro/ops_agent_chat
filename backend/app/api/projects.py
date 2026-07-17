from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator
from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_environment, require_environment_permission, require_project, require_project_permission
from app.context.collectors.manual import collect_manual_services
from app.context.jobs import collector_run_out, queue_environment_collectors
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
    name: str = Field(default="default", min_length=1, max_length=80)
    runtime_type: Literal["manual", "docker_compose", "kubernetes", "systemd", "mixed"] = "manual"
    connection_id: int | None = None
    workdir: str | None = Field(default=None, max_length=500)
    namespace: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] = Field(default_factory=dict)
    policy_profile: Literal["development", "test", "staging", "production"] = "development"
    is_default: bool = False

    @model_validator(mode="after")
    def validate_config(self):
        self.config_json = validate_runtime_config(self.runtime_type, self.config_json)
        return self


class EnvironmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    runtime_type: Literal["manual", "docker_compose", "kubernetes", "systemd", "mixed"] | None = None
    connection_id: int | None = None
    workdir: str | None = Field(default=None, max_length=500)
    namespace: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] | None = None
    policy_profile: Literal["development", "test", "staging", "production"] | None = None
    is_default: bool | None = None


ShortName = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")]


def validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value or "\n" in value:
        raise ValueError("Path must be relative and stay inside the environment workdir")
    return value


RelativePath = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(validate_relative_path)]


class RegisteredDeploymentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: ShortName
    manifest: RelativePath | None = None
    rollback: Literal["stop", "restart", "rollout_undo"]
    expected_instances: int = Field(default=1, ge=1, le=100)


class RegisteredConfigChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: RelativePath
    content: str = Field(max_length=2_000_000)
    current_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    allow_create: bool = False

    @model_validator(mode="after")
    def require_precondition(self):
        if not self.current_sha256 and not self.allow_create:
            raise ValueError("current_sha256 or allow_create=true is required")
        return self


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    known_services: list[ShortName] = Field(default_factory=list, max_length=500)
    services: list[ShortName] | None = Field(default=None, max_length=500)
    health_endpoints: dict[ShortName, HttpUrl] = Field(default_factory=dict)
    health_success_statuses: dict[ShortName, list[Annotated[int, Field(ge=100, le=599)]]] = Field(default_factory=dict)
    registered_deployments: dict[ShortName, RegisteredDeploymentConfig] = Field(default_factory=dict)
    registered_config_changes: dict[ShortName, RegisteredConfigChange] = Field(default_factory=dict)
    manual_entities: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    manual_relationships: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    context_files: list[RelativePath] = Field(default_factory=list, max_length=100)
    nginx_config_files: list[RelativePath] = Field(default_factory=list, max_length=100)

    @field_validator("health_endpoints")
    @classmethod
    def validate_health_endpoints(cls, value: dict[str, HttpUrl]) -> dict[str, HttpUrl]:
        for endpoint in value.values():
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("Health endpoints cannot contain credentials, query strings or fragments")
        return value


class DockerComposeRuntimeConfig(RuntimeConfig):
    compose_file: RelativePath = "docker-compose.yml"

    @model_validator(mode="after")
    def validate_deployments(self):
        if any(item.rollback not in {"stop", "restart"} for item in self.registered_deployments.values()):
            raise ValueError("Docker Compose deployment rollback must be stop or restart")
        return self


class KubernetesRuntimeConfig(RuntimeConfig):
    @model_validator(mode="after")
    def validate_deployments(self):
        if any(not item.manifest for item in self.registered_deployments.values()):
            raise ValueError("Kubernetes registered deployments require a manifest")
        if any(item.rollback != "rollout_undo" for item in self.registered_deployments.values()):
            raise ValueError("Kubernetes registered deployments require rollback=rollout_undo")
        return self


class SystemdRuntimeConfig(RuntimeConfig):
    registered_deployments: dict = Field(default_factory=dict, max_length=0)


class ManualRuntimeConfig(RuntimeConfig):
    registered_deployments: dict = Field(default_factory=dict, max_length=0)


class MixedRuntimeConfig(DockerComposeRuntimeConfig):
    pass


def validate_runtime_config(runtime_type: str, value: dict[str, Any]) -> dict[str, Any]:
    model = {
        "manual": ManualRuntimeConfig,
        "docker_compose": DockerComposeRuntimeConfig,
        "kubernetes": KubernetesRuntimeConfig,
        "systemd": SystemdRuntimeConfig,
        "mixed": MixedRuntimeConfig,
    }.get(runtime_type)
    if not model:
        raise ValueError(f"Unsupported runtime type: {runtime_type}")
    config = model.model_validate(value)
    return config.model_dump(mode="json", exclude_none=True)


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
    if payload.is_default:
        db.execute(update(Environment).where(Environment.project_id == project_id, Environment.is_default.is_(True)).values(is_default=False))
    row = Environment(project_id=project_id, **payload.model_dump()); db.add(row)
    try:
        db.flush(); collect_manual_services(db, row); db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Environment name or default selection conflicts with an existing environment") from exc
    db.refresh(row); return environment_out(row)


@router.patch("/environments/{environment_id}")
def patch_environment(environment_id: int, payload: EnvironmentPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment_permission(db, user, environment_id, "project.manage")
    project = db.get(Project, row.project_id)
    if "connection_id" in payload.model_fields_set:
        validate_connection_scope(db, project, payload.connection_id)
    if payload.config_json is not None or payload.runtime_type is not None:
        runtime_type = payload.runtime_type or row.runtime_type
        config_json = payload.config_json if payload.config_json is not None else row.config_json
        try:
            validated_config = validate_runtime_config(runtime_type, config_json or {})
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "Environment configuration is invalid for the selected runtime") from exc
        if payload.config_json is not None:
            payload.config_json = validated_config
        else:
            row.config_json = validated_config
    if payload.is_default is True:
        db.execute(update(Environment).where(Environment.project_id == row.project_id, Environment.id != row.id, Environment.is_default.is_(True)).values(is_default=False))
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Environment name or default selection conflicts with an existing environment") from exc
    db.refresh(row); return environment_out(row)


@router.delete("/environments/{environment_id}")
def delete_environment(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment_permission(db, user, environment_id, "project.manage")
    replacement = db.scalar(
        select(Environment)
        .where(
            Environment.project_id == row.project_id,
            Environment.id != row.id,
            Environment.is_active.is_(True),
        )
        .order_by(Environment.updated_at.desc())
        .limit(1)
    )
    if not replacement:
        raise HTTPException(409, "A project must keep at least one active environment")
    was_default = row.is_default; row.is_active = False; row.is_default = False
    # Flush the old default first so the partial unique index never observes two
    # active defaults during the replacement update.
    db.flush()
    if was_default:
        replacement.is_default = True
    db.commit(); return environment_out(row)


@router.post("/environments/{environment_id}/test-connection")
def test_connection(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment(db, user, environment_id); connection = db.get(Connection, row.connection_id)
    if not connection: raise HTTPException(400, "Environment has no connection")
    ok, message = SSHTransport().test_connection(connection); connection.status = "connected" if ok else "failed"; db.commit(); return {"ok": ok, "message": message}


@router.post("/environments/{environment_id}/collect-context", status_code=202)
def collect_context(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = require_environment_permission(db, user, environment_id, "project.manage")
    return [collector_run_out(item) for item in queue_environment_collectors(db, row, user.id)]


@router.get("/environments/{environment_id}/collector-runs")
def collector_runs(environment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_environment(db, user, environment_id)
    return [collector_run_out(item) for item in db.scalars(select(CollectorRun).where(CollectorRun.environment_id == environment_id).order_by(CollectorRun.created_at.desc()).limit(100)).all()]


@router.post("/collector-runs/{run_id}/cancel")
def cancel_collector_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = db.get(CollectorRun, run_id)
    if not run:
        raise HTTPException(404, "Collector run not found")
    require_environment_permission(db, user, run.environment_id, "project.manage")
    if run.status in {"completed", "failed", "cancelled"}:
        return collector_run_out(run)
    now = datetime.now(timezone.utc)
    claimed = db.scalar(
        update(CollectorRun)
        .where(CollectorRun.id == run.id, CollectorRun.status.in_(["queued", "running"]))
        .values(
            cancel_requested_at=now,
            status=case((CollectorRun.status == "queued", "cancelled"), else_=CollectorRun.status),
            finished_at=case((CollectorRun.status == "queued", now), else_=CollectorRun.finished_at),
            lease_owner=case((CollectorRun.status == "queued", None), else_=CollectorRun.lease_owner),
            lease_expires_at=case((CollectorRun.status == "queued", None), else_=CollectorRun.lease_expires_at),
        )
        .returning(CollectorRun.id)
    )
    db.commit()
    if not claimed:
        db.expire(run)
    db.refresh(run)
    return collector_run_out(run)
