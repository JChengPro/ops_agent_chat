from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.registry import registry
from app.context.collectors.manual import collect_manual_services
from app.core.config import get_settings
from app.core.security import hash_password
from app.experience.service import index_experience
from app.models.experience import ExperienceItem
from app.models.project import Connection, Environment, Project, ProjectMember
from app.models.user import User


DEFAULT_SERVICES = ["backend", "worker", "frontend", "mobile-frontend", "redis", "mysql", "rabbitmq"]


def seed_initial_data(db: Session) -> None:
    settings = get_settings()
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if not user:
        user = User(username=settings.admin_username, email=settings.admin_email, password_hash=hash_password(settings.admin_password), role="admin", is_active=True)
        db.add(user)
        db.flush()
    connection = db.scalar(select(Connection).where(Connection.owner_id == user.id, Connection.name == "videohub-local"))
    if not connection:
        connection = Connection(
            owner_id=user.id,
            name="videohub-local",
            connection_type="ssh",
            host=settings.videohub_ssh_host,
            port=settings.videohub_ssh_port,
            username=settings.videohub_ssh_username,
            credential_ref=settings.videohub_ssh_key_path or None,
            host_fingerprint=settings.videohub_ssh_host_fingerprint or None,
        )
        db.add(connection)
        db.flush()
    project = db.scalar(select(Project).where(Project.owner_id == user.id, Project.name == settings.videohub_project_name))
    if not project:
        project = Project(owner_id=user.id, name=settings.videohub_project_name, description="VideoHub operations project", settings_json={})
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    environment = db.scalar(select(Environment).where(Environment.project_id == project.id, Environment.name == "default"))
    if not environment:
        health_url = settings.videohub_health_url.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
        environment = Environment(
            project_id=project.id,
            name="default",
            runtime_type=settings.videohub_deploy_type,
            connection_id=connection.id,
            workdir=settings.videohub_workdir,
            config_json={"compose_file": settings.videohub_compose_file, "health_endpoints": {"default": health_url}, "known_services": DEFAULT_SERVICES},
            policy_profile="development",
            is_default=True,
        )
        db.add(environment)
        db.flush()
        collect_manual_services(db, environment)
    registry.sync_versions(db)
    _seed_experience(db, project.id, user.id)
    db.commit()


def _seed_experience(db: Session, project_id: int, user_id: int) -> None:
    if db.scalar(select(ExperienceItem.id).where(ExperienceItem.project_id == project_id).limit(1)):
        return
    for path in _knowledge_files():
        content = path.read_text(encoding="utf-8", errors="replace")
        item = ExperienceItem(
            project_id=project_id,
            title=path.stem,
            item_type="project_document",
            content=content,
            tags=["bootstrap", "project"],
            source_type="file",
            source_ref=str(path),
            trust_status="verified",
            created_by=user_id,
            verified_by=user_id,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.flush()
        index_experience(db, item)


def _knowledge_files() -> list[Path]:
    for root in (Path("docs/knowledge/videohub"), Path("../docs/knowledge/videohub")):
        if root.exists():
            return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".txt"})
    return []
