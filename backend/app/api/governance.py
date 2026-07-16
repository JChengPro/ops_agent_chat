from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_project
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.governance import AuditEvent, MessageFeedback
from app.models.user import User
from app.audit.service import verify_audit_chain

router = APIRouter(tags=["governance"])


class FeedbackPayload(BaseModel):
    rating: str = Field(pattern="^(helpful|incomplete|inaccurate|unresolved)$")
    reason_code: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: int, payload: FeedbackPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    message = db.get(ChatMessage, message_id); session = db.get(ChatSession, message.session_id) if message else None
    if not message or not session or session.user_id != user.id or message.role != "assistant": raise HTTPException(404, "Assistant message not found")
    run_id = (message.metadata_json or {}).get("run_id")
    row = db.scalar(select(MessageFeedback).where(MessageFeedback.message_id == message_id))
    if row:
        row.rating = payload.rating; row.reason_code = payload.reason_code; row.comment = payload.comment
    else:
        row = MessageFeedback(message_id=message_id, run_id=run_id, project_id=message.project_id, user_id=user.id, rating=payload.rating, reason_code=payload.reason_code, comment=payload.comment); db.add(row)
    db.commit(); db.refresh(row); return row


@router.get("/projects/{project_id}/feedback")
def project_feedback(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id); return db.scalars(select(MessageFeedback).where(MessageFeedback.project_id == project_id).order_by(MessageFeedback.created_at.desc())).all()


@router.get("/projects/{project_id}/audit-events")
def audit(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id); return db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.created_at.desc()).limit(500)).all()


@router.get("/audit-events/verify")
def verify_audit(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Administrator permission is required")
    return verify_audit_chain(db)
