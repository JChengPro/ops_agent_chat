from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.project import Project
from app.models.server import Server
from app.models.user import User
from app.rag.indexer import bootstrap_project_knowledge


def seed_initial_data(db: Session) -> None:
    settings = get_settings()
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if not user:
        user = User(
            username=settings.admin_username,
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.flush()

    server = db.scalar(select(Server).where(Server.name == "videohub-local"))
    if not server:
        server = Server(
            owner_id=user.id,
            name="videohub-local",
            host=settings.videohub_ssh_host,
            port=settings.videohub_ssh_port,
            username=settings.videohub_ssh_username,
            auth_type="key",
            private_key_ref=settings.videohub_ssh_key_path or None,
        )
        db.add(server)
        db.flush()

    project = db.scalar(select(Project).where(Project.name == settings.videohub_project_name))
    if not project:
        project = Project(
            owner_id=user.id,
            server_id=server.id,
            name=settings.videohub_project_name,
            description="Bootstrap V1 project for VideoHub local diagnosis.",
            deploy_type=settings.videohub_deploy_type,
            workdir=settings.videohub_workdir,
            compose_file=settings.videohub_compose_file,
            health_url=settings.videohub_health_url,
            allowed_container_prefixes=["videohub-"],
            known_services=[
                "videohub-backend-1",
                "videohub-worker-1",
                "videohub-frontend-1",
                "videohub-mobile-frontend-1",
                "videohub-redis-1",
                "videohub-mysql-1",
                "videohub-rabbitmq-1",
            ],
            settings_json={"v1_read_only": True},
        )
        db.add(project)
        db.flush()

    db.commit()

    knowledge_path = _resolve_knowledge_path()
    bootstrap_project_knowledge(db, project_id=project.id, user_id=user.id, knowledge_path=knowledge_path)
    db.commit()


def _resolve_knowledge_path() -> Path:
    candidates = [
        Path("docs/knowledge/videohub"),
        Path("../docs/knowledge/videohub"),
        get_settings().knowledge_root / "videohub",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
