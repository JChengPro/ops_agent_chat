from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.project import Project
from app.models.server import Server
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectOut, ServerOut

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Project]:
    return list(db.scalars(select(Project).where(Project.owner_id == user.id, Project.is_active.is_(True)).order_by(Project.id)))


@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Project:
    server = db.get(Server, payload.server_id)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    project = Project(owner_id=user.id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/server", response_model=ServerOut)
def get_project_server(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Server:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    server = db.get(Server, project.server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

