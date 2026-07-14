from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import require_project, require_project_permission
from app.api.projects import validate_connection_scope
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.project import Connection, Project, ProjectMember
from app.models.user import User
from app.services.seed_service import seed_initial_data


def test_viewer_can_read_but_cannot_manage_project():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        seed_initial_data(db)
        project = db.scalar(select(Project).limit(1))
        viewer = User(username=f"viewer-{suffix}", email=f"viewer-{suffix}@example.test", password_hash=hash_password("test"), role="user", is_active=True)
        db.add(viewer)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        db.commit()
        assert require_project(db, viewer, project.id).id == project.id
        with pytest.raises(HTTPException) as exc:
            require_project_permission(db, viewer, project.id, "project.manage")
        assert exc.value.status_code == 403


def test_environment_connection_must_belong_to_project_owner():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        seed_initial_data(db)
        project = db.scalar(select(Project).limit(1))
        other = User(username=f"other-{suffix}", email=f"other-{suffix}@example.test", password_hash=hash_password("test"), role="user", is_active=True)
        db.add(other)
        db.flush()
        connection = Connection(owner_id=other.id, name=f"other-{suffix}", connection_type="ssh", host="127.0.0.1", port=22)
        db.add(connection)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            validate_connection_scope(db, project, connection.id)
        assert exc.value.status_code == 400
