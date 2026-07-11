from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import AgentPipeline
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, ChatSession
from app.models.project import Project
from app.models.user import User
from app.schemas.chat import ChatMessageOut, ChatSendRequest, ChatSendResponse, ChatSessionCreate, ChatSessionOut, ChatSessionUpdate

router = APIRouter(tags=["chat"])
pipeline = AgentPipeline()


@router.get("/projects/{project_id}/chat-sessions", response_model=list[ChatSessionOut])
def list_sessions(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ChatSession]:
    _ensure_project(db, project_id, user.id)
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.project_id == project_id, ChatSession.user_id == user.id, ChatSession.status != "deleted")
            .order_by(ChatSession.is_pinned.desc(), ChatSession.updated_at.desc(), ChatSession.id.desc())
        )
    )


@router.post("/projects/{project_id}/chat-sessions", response_model=ChatSessionOut)
def create_session(
    project_id: int,
    payload: ChatSessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatSession:
    _ensure_project(db, project_id, user.id)
    session = ChatSession(project_id=project_id, user_id=user.id, title=payload.title or "新会话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.patch("/chat-sessions/{session_id}", response_model=ChatSessionOut)
def update_session(
    session_id: int,
    payload: ChatSessionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatSession:
    session = _ensure_session(db, session_id, user.id)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Session title cannot be empty")
        session.title = title[:200]
    if payload.is_pinned is not None:
        session.is_pinned = payload.is_pinned
    db.commit()
    db.refresh(session)
    return session


@router.delete("/chat-sessions/{session_id}", response_model=ChatSessionOut)
def delete_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ChatSession:
    session = _ensure_session(db, session_id, user.id)
    session.status = "deleted"
    db.commit()
    db.refresh(session)
    return session


@router.get("/chat-sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def list_messages(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ChatMessage]:
    session = _ensure_session(db, session_id, user.id)
    return list(
        db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc()))
    )


@router.post("/chat-sessions/{session_id}/messages", response_model=ChatSendResponse)
def send_message(
    session_id: int,
    payload: ChatSendRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatSendResponse:
    session = _ensure_session(db, session_id, user.id)
    user_message = ChatMessage(
        session_id=session.id,
        project_id=session.project_id,
        role="user",
        content=payload.content,
        message_type="text",
        metadata_json={},
    )
    db.add(user_message)
    db.flush()
    response = pipeline.handle_user_message(db, session, user_message)
    if session.title == "新会话":
        session.title = payload.content[:40]
    db.commit()
    db.refresh(response.assistant_message)
    return ChatSendResponse(
        assistant_message=ChatMessageOut.model_validate(response.assistant_message),
        command_runs=[_run_to_dict(run) for run in response.command_runs],
        command_plan=response.command_plan,
        experience_sources=response.rag_sources,
        rag_sources=response.rag_sources,
        approval_request=None,
    )


def _ensure_project(db: Session, project_id: int, user_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user_id or not project.is_active:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _ensure_session(db: Session, session_id: int, user_id: int) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if not session or session.status == "deleted":
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_project(db, session.project_id, user_id)
    return session


def _run_to_dict(run) -> dict:
    return {
        "id": run.id,
        "command": run.command,
        "cwd": run.cwd,
        "purpose": run.purpose,
        "risk_level": run.risk_level,
        "status": run.status,
        "exit_code": run.exit_code,
        "stdout_excerpt": run.stdout_excerpt,
        "stderr_excerpt": run.stderr_excerpt,
        "duration_ms": run.duration_ms,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "ruleguard_result": run.ruleguard_result,
    }
