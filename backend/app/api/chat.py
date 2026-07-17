from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.service import create_run, message_out
from app.api.deps import require_project
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, ChatSession
from app.models.project import Environment
from app.models.user import User

router = APIRouter(tags=["chat"])


class SessionCreate(BaseModel):
    title: str = Field(default="New chat", max_length=200)
    environment_id: int | None = None


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


def session_out(item: ChatSession) -> dict:
    return {"id": item.id, "project_id": item.project_id, "environment_id": item.environment_id, "user_id": item.user_id, "title": item.title, "status": item.status, "is_pinned": item.is_pinned, "created_at": item.created_at, "updated_at": item.updated_at}


@router.get("/projects/{project_id}/chat-sessions")
def project_sessions(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    return [session_out(item) for item in db.scalars(select(ChatSession).where(ChatSession.project_id == project_id, ChatSession.user_id == user.id, ChatSession.status != "deleted").order_by(ChatSession.is_pinned.desc(), ChatSession.updated_at.desc())).all()]


@router.post("/projects/{project_id}/chat-sessions")
def create_project_session(project_id: int, payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    environment_id = payload.environment_id or db.scalar(select(Environment.id).where(Environment.project_id == project_id, Environment.is_active.is_(True)).order_by(Environment.is_default.desc()).limit(1))
    environment = db.get(Environment, environment_id) if environment_id else None
    if not environment or environment.project_id != project_id or not environment.is_active:
        raise HTTPException(400, "Environment does not belong to the selected project")
    row = ChatSession(project_id=project_id, environment_id=environment_id, user_id=user.id, title=payload.title)
    db.add(row); db.commit(); db.refresh(row); return session_out(row)


@router.get("/chat-sessions")
def general_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [session_out(item) for item in db.scalars(select(ChatSession).where(ChatSession.project_id.is_(None), ChatSession.user_id == user.id, ChatSession.status != "deleted").order_by(ChatSession.is_pinned.desc(), ChatSession.updated_at.desc())).all()]


@router.post("/chat-sessions")
def create_general_session(payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = ChatSession(project_id=None, environment_id=None, user_id=user.id, title=payload.title)
    db.add(row); db.commit(); db.refresh(row); return session_out(row)


@router.patch("/chat-sessions/{session_id}")
def patch_session(session_id: int, payload: SessionPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ChatSession, session_id)
    if not row or row.user_id != user.id: raise HTTPException(404, "Chat session not found")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return session_out(row)


@router.delete("/chat-sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ChatSession, session_id)
    if not row or row.user_id != user.id: raise HTTPException(404, "Chat session not found")
    row.status = "deleted"; db.commit(); return session_out(row)


@router.get("/chat-sessions/{session_id}/messages")
def messages(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ChatSession, session_id)
    if not row or row.user_id != user.id: raise HTTPException(404, "Chat session not found")
    return [message_out(item) for item in db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at, ChatMessage.id)).all()]


@router.post("/chat-sessions/{session_id}/messages", deprecated=True)
def send_message(session_id: int, payload: MessageCreate, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = db.get(ChatSession, session_id)
    if not session or session.user_id != user.id or session.status == "deleted": raise HTTPException(404, "Chat session not found")
    if session.project_id:
        require_project(db, user, session.project_id)
        environment = db.get(Environment, session.environment_id) if session.environment_id else None
        if not environment or not environment.is_active or environment.project_id != session.project_id:
            raise HTTPException(409, "The chat environment is no longer active")
    response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Wed, 31 Dec 2026 23:59:59 GMT"
    response.headers["Link"] = f'</api/chat-sessions/{session_id}/agent-runs>; rel="successor-version"'
    return create_run(db, session, user.id, payload.content, payload.client_request_id)


@router.post("/chat-sessions/{session_id}/agent-runs")
def queue_message(session_id: int, payload: MessageCreate, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = db.get(ChatSession, session_id)
    if not session or session.user_id != user.id or session.status == "deleted":
        raise HTTPException(404, "Chat session not found")
    if session.project_id:
        require_project(db, user, session.project_id)
        environment = db.get(Environment, session.environment_id) if session.environment_id else None
        if not environment or not environment.is_active or environment.project_id != session.project_id:
            raise HTTPException(409, "The chat environment is no longer active")
    response.status_code = status.HTTP_202_ACCEPTED
    return create_run(db, session, user.id, payload.content, payload.client_request_id)
