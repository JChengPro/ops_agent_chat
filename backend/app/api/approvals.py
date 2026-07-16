from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.agent.service import approval_out, run_out
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
    approver_projects = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id, ProjectMember.role.in_(["owner", "approver"]))
    rows = db.execute(
        select(Approval, Action)
        .join(Action)
        .join(Project, Project.id == Action.project_id)
        .where(
            Approval.decision == status,
            or_(Approval.requested_from == user.id, Project.owner_id == user.id, Action.project_id.in_(approver_projects)),
        )
        .order_by(Approval.created_at.desc())
    ).all()
    return [approval_out(approval, action) for approval, action in rows]


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    approval, action, _ = require_approval(db, user, approval_id); return approval_out(approval, action)


def decide(approval_id: str, payload: ApprovalDecision, decision: str, db: Session, user: User):
    approval, action, run = require_approval(db, user, approval_id)
    now = datetime.now(timezone.utc)
    if approval.decision != "pending": raise HTTPException(409, "Approval has already been decided")
    if approval.expires_at <= now: approval.decision = "expired"; action.status = "expired"; db.commit(); raise HTTPException(409, "Approval has expired")
    if payload.action_hash != approval.action_hash or payload.action_hash != action.action_hash: raise HTTPException(409, "Action has changed; approval is invalid")
    claimed = db.scalar(
        update(Approval)
        .where(Approval.id == approval.id, Approval.decision == "pending", Approval.action_hash == payload.action_hash)
        .values(decision=decision, comment=payload.comment, decided_at=now)
        .returning(Approval.id)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(409, "Approval has already been decided")
    action.status = "approved" if decision == "approved" else "rejected"
    append_audit_event(db, actor_type="user", actor_id=user.id, event_type=f"approval.{decision}", payload={"approval_id": approval.id, "action_hash": approval.action_hash}, project_id=action.project_id, environment_id=action.environment_id, run_id=run.id, action_id=action.id)
    db.commit()
    pending = db.scalar(select(Approval.id).join(Action).where(Action.run_id == run.id, Approval.decision == "pending").limit(1))
    if not pending:
        queued = db.scalar(
            update(AgentRun)
            .where(AgentRun.id == run.id, AgentRun.status == "waiting_for_approval")
            .values(status="queued", current_step="queued_resume", started_at=None, lease_owner=None, lease_expires_at=None)
            .returning(AgentRun.id)
        )
        if queued:
            db.commit()
            db.refresh(run)
    db.refresh(approval)
    db.refresh(action)
    return {"approval": approval_out(approval, action), "run_summary": run_out(run)}


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, "approved", db, user)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, payload: ApprovalDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, "rejected", db, user)
