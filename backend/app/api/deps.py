from fastapi import Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.project import Environment, Project, ProjectMember
from app.models.user import User
from app.policy.engine import permissions_for_role


def require_project(db: Session, user: User, project_id: int) -> Project:
    project = db.scalar(
        select(Project).outerjoin(ProjectMember).where(
            Project.id == project_id,
            Project.is_active.is_(True),
            or_(Project.owner_id == user.id, ProjectMember.user_id == user.id),
        )
    )
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def require_project_permission(db: Session, user: User, project_id: int, permission: str) -> Project:
    project = require_project(db, user, project_id)
    role = "owner" if project.owner_id == user.id else db.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
    )
    if permission not in permissions_for_role(role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Project permission is required: {permission}")
    return project


def require_environment(db: Session, user: User, environment_id: int) -> Environment:
    environment = db.get(Environment, environment_id)
    if not environment or not environment.is_active:
        raise HTTPException(status_code=404, detail="Environment not found")
    require_project(db, user, environment.project_id)
    return environment


def require_environment_permission(db: Session, user: User, environment_id: int, permission: str) -> Environment:
    environment = require_environment(db, user, environment_id)
    require_project_permission(db, user, environment.project_id, permission)
    return environment
