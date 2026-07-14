from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.service import approval_out, resume_run
from app.api.agent_runs import require_run
from app.api.deps import require_project
from app.audit.service import append_audit_event
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.user import User
from app.models.project import Project, ProjectMember
from app.policy.engine import permissions_for_role

router = APIRouter(tags=["approvals"])


class ApprovalDecision(BaseModel):
    action_hash: str
    comment: str | None = None


def require_approval(db: Session, user: User, approval_id: str) -> tuple[Approval, Action, AgentRun]:
    approval = db.get(Approval, approval_id)
    action = db.get(Action, approval.action_id) if approval else None
    run = db.get(AgentRun, action.run_id) if action else None
    if not approval or not action or not run: raise HTTPException(404, "Approval not found")
    if run.status != "waiting_for_approval": raise HTTPException(409, "Agent run is not waiting for approval")
    if action.project_id:
        project = require_project(db, user, action.project_id)
        role = "owner" if project.owner_id == user.id else db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
        if "approval.decide" not in permissions_for_role(role): raise HTTPException(403, "Approval permission is required")
    return approval, action, run


@router.get("/approvals")
def list_approvals(status: str = "pending", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(Approval, Action).join(Action).where(Approval.requested_from == user.id, Approval.decision == status).order_by(Approval.created_at.desc())).all()
    return [approval_out(approval, action) for approval, action in rows]


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    approval, action, _ = require_approval(db, user, approval_id); return approval_out(approval, action)


def decide(approval_id: str, payload: ApprovalDecision, request: Request, decision: str, db: Session, user: User):
    approval, action, run = require_approval(db, user, approval_id)
    now = datetime.now(timezone.utc)
    if approval.decision != "pending": raise HTTPException(409, "Approval has already been decided")
    if approval.expires_at <= now: approval.decision = "expired"; action.status = "expired"; db.commit(); raise HTTPException(409, "Approval has expired")
    if payload.action_hash != approval.action_hash or payload.action_hash != action.action_hash: raise HTTPException(409, "Action has changed; approval is invalid")
    approval.decision = decision; approval.comment = payload.comment; approval.decided_at = now
    action.status = "approved" if decision == "approved" else "rejected"
    append_audit_event(db, actor_type="user", actor_id=user.id, event_type=f"approval.{decision}", payload={"approval_id": approval.id, "action_hash": approval.action_hash}, project_id=action.project_id, environment_id=action.environment_id, run_id=run.id, action_id=action.id)
    db.commit()
    pending = db.scalar(select(Approval.id).join(Action).where(Action.run_id == run.id, Approval.decision == "pending").limit(1))
    if not pending: return resume_run(db, request.app.state.ops_agent, run)
    return {"approval": approval_out(approval, action), "run_summary": {"id": run.id, "status": run.status}}


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecision, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, request, "approved", db, user)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, payload: ApprovalDecision, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, request, "rejected", db, user)
