from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.command import CommandRun
from app.models.project import Project
from app.models.user import User
from app.schemas.command import CommandRunOut

router = APIRouter(tags=["commands"])


@router.get("/projects/{project_id}/command-runs", response_model=list[CommandRunOut])
def list_command_runs(
    project_id: int,
    session_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CommandRun]:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    query = select(CommandRun).where(CommandRun.project_id == project_id)
    if session_id:
        query = query.where(CommandRun.session_id == session_id)
    return list(db.scalars(query.order_by(CommandRun.created_at.desc()).limit(100)))


@router.get("/command-runs/{command_run_id}", response_model=CommandRunOut)
def get_command_run(command_run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CommandRun:
    run = db.get(CommandRun, command_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Command run not found")
    project = db.get(Project, run.project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Command run not found")
    return run

