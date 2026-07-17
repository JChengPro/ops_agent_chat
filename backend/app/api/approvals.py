from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.agent.service import approval_out, run_out
from app.api.deps import require_project
from app.audit.service import append_audit_event
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage
from app.models.user import User
from app.models.project import Project, ProjectMember
from app.policy.engine import permissions_for_role
from app.agent.status import close_pending_approval_batch, queue_run_resume
from app.policy.action_hash import action_snapshot, compute_action_hash

router = APIRouter(tags=["approvals"])


class ApprovalDecision(BaseModel):
    action_hash: str
    comment: str | None = None


class ApprovalBatchItem(BaseModel):
    approval_id: str
    action_hash: str


class ApprovalBatchDecision(BaseModel):
    approvals: list[ApprovalBatchItem] = Field(min_length=1, max_length=50)
    selected_approval_ids: list[str] | None = Field(default=None, max_length=50)
    comment: str | None = None

    @model_validator(mode="after")
    def validate_unique_approvals(self):
        ids = [item.approval_id for item in self.approvals]
        if len(ids) != len(set(ids)):
            raise ValueError("Approval IDs must be unique")
        selected = self.selected_approval_ids or []
        if len(selected) != len(set(selected)):
            raise ValueError("Selected approval IDs must be unique")
        return self


def require_approval(db: Session, user: User, approval_id: str, *, require_waiting: bool = False) -> tuple[Approval, Action, AgentRun]:
    approval = db.get(Approval, approval_id)
    action = db.get(Action, approval.action_id) if approval else None
    run = db.get(AgentRun, action.run_id) if action else None
    if not approval or not action or not run: raise HTTPException(404, "Approval not found")
    if require_waiting and run.status != "waiting_for_approval": raise HTTPException(409, "Agent run is not waiting for approval")
    if action.project_id:
        project = require_project(db, user, action.project_id)
        role = "owner" if project.owner_id == user.id else db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
        if "approval.decide" not in permissions_for_role(role): raise HTTPException(403, "Approval permission is required")
    elif run.user_id != user.id:
        raise HTTPException(404, "Approval not found")
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


def sync_approval_message(db: Session, run: AgentRun) -> None:
    if not run.assistant_message_id:
        return
    message = db.get(ChatMessage, run.assistant_message_id)
    if not message:
        return
    rows = db.execute(
        select(Approval, Action)
        .join(Action)
        .where(Action.run_id == run.id)
        .order_by(Approval.created_at, Approval.id)
    ).all()
    metadata = dict(message.metadata_json or {})
    metadata["run_status"] = run.status
    metadata["approvals"] = [approval_out(approval, action) for approval, action in rows]
    message.metadata_json = metadata


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    approval, action, _ = require_approval(db, user, approval_id); return approval_out(approval, action)


def decide(approval_id: str, payload: ApprovalDecision, decision: str, db: Session, user: User):
    approval, action, run = require_approval(db, user, approval_id)
    run = db.scalar(select(AgentRun).where(AgentRun.id == run.id).with_for_update())
    if not run or run.status != "waiting_for_approval":
        raise HTTPException(409, "Agent run is not waiting for approval")
    db.refresh(approval)
    db.refresh(action)
    now = datetime.now(timezone.utc)
    if approval.decision != "pending": raise HTTPException(409, "Approval has already been decided")
    if approval.expires_at <= now:
        close_pending_approval_batch(db, run.id, decision="expired", reason_code="APPROVAL_EXPIRED", comment="Approval validity period expired", action_status="expired")
        queue_run_resume(db, run.id)
        db.commit()
        raise HTTPException(409, "Approval has expired")
    if approval.action_hash != action.action_hash or action.action_hash != compute_action_hash(action_snapshot(action)):
        close_pending_approval_batch(db, run.id, decision="invalidated", reason_code="ACTION_HASH_MISMATCH", comment="Action snapshot no longer matches approval", action_status="approval_invalid")
        queue_run_resume(db, run.id)
        db.commit()
        raise HTTPException(409, "Action has changed; approval is invalid")
    if payload.action_hash != approval.action_hash:
        raise HTTPException(409, "The submitted Action Hash does not match this approval")
    claimed = db.scalar(
        update(Approval)
        .where(Approval.id == approval.id, Approval.decision == "pending", Approval.action_hash == payload.action_hash)
        .values(decision=decision, reason_code=f"USER_{decision.upper()}", comment=payload.comment, decided_by=user.id, decided_at=now)
        .returning(Approval.id)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(409, "Approval has already been decided")
    action.status = "approved" if decision == "approved" else "rejected"
    if decision == "rejected":
        close_pending_approval_batch(
            db,
            run.id,
            decision="cancelled",
            reason_code="APPROVAL_BATCH_REJECTED",
            comment="Another action in this approval batch was rejected",
            action_status="cancelled",
            decided_by=user.id,
            exclude_approval_id=approval.id,
        )
    append_audit_event(db, actor_type="user", actor_id=user.id, event_type=f"approval.{decision}", payload={"approval_id": approval.id, "action_hash": approval.action_hash}, project_id=action.project_id, environment_id=action.environment_id, run_id=run.id, action_id=action.id)
    db.flush()
    pending = db.scalar(select(Approval.id).join(Action).where(Action.run_id == run.id, Approval.decision == "pending").limit(1))
    if not pending:
        if not queue_run_resume(db, run.id):
            db.rollback()
            raise HTTPException(409, "Approval could not resume the Agent run")
        db.refresh(run)
    sync_approval_message(db, run)
    db.commit()
    db.refresh(approval)
    db.refresh(action)
    return {"approval": approval_out(approval, action), "run_summary": run_out(run)}


def decide_batch(run_id: str, payload: ApprovalBatchDecision, decision: str, db: Session, user: User):
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404, "Agent run not found")
    if run.project_id:
        project = require_project(db, user, run.project_id)
        role = "owner" if project.owner_id == user.id else db.scalar(
            select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
        )
        if "approval.decide" not in permissions_for_role(role):
            raise HTTPException(403, "Approval permission is required")
    elif run.user_id != user.id:
        raise HTTPException(404, "Agent run not found")

    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if not run or run.status != "waiting_for_approval":
        raise HTTPException(409, "Agent run is not waiting for approval")

    rows = db.execute(
        select(Approval, Action)
        .join(Action)
        .where(Action.run_id == run.id, Approval.decision == "pending")
        .order_by(Approval.created_at, Approval.id)
        .with_for_update()
    ).all()
    submitted = {item.approval_id: item.action_hash for item in payload.approvals}
    pending_ids = {approval.id for approval, _ in rows}
    if not rows or set(submitted) != pending_ids:
        raise HTTPException(409, "Approval batch has changed; refresh and review it again")
    if decision == "approved":
        selected_ids = pending_ids if payload.selected_approval_ids is None else set(payload.selected_approval_ids)
        if not selected_ids.issubset(pending_ids):
            raise HTTPException(409, "Selected approvals are not part of the current batch")
    else:
        selected_ids = set()

    now = datetime.now(timezone.utc)
    if any(approval.expires_at <= now for approval, _ in rows):
        close_pending_approval_batch(
            db,
            run.id,
            decision="expired",
            reason_code="APPROVAL_EXPIRED",
            comment="Approval validity period expired",
            action_status="expired",
        )
        queue_run_resume(db, run.id)
        db.commit()
        raise HTTPException(409, "Approval batch has expired")

    invalid_snapshot = any(
        submitted[approval.id] != approval.action_hash
        or approval.action_hash != action.action_hash
        or action.action_hash != compute_action_hash(action_snapshot(action))
        for approval, action in rows
    )
    if invalid_snapshot:
        close_pending_approval_batch(
            db,
            run.id,
            decision="invalidated",
            reason_code="ACTION_HASH_MISMATCH",
            comment="Action snapshot no longer matches approval batch",
            action_status="approval_invalid",
        )
        queue_run_resume(db, run.id)
        db.commit()
        raise HTTPException(409, "An Action changed; the approval batch is invalid")

    for approval, action in rows:
        selected = approval.id in selected_ids
        approval_decision = "approved" if selected else "rejected"
        action_status = "approved" if selected else "rejected"
        approval.decision = approval_decision
        approval.reason_code = (
            "USER_BATCH_APPROVED"
            if selected
            else "USER_BATCH_NOT_SELECTED" if decision == "approved" else "USER_BATCH_REJECTED"
        )
        approval.comment = payload.comment
        approval.decided_by = user.id
        approval.decided_at = now
        action.status = action_status
        append_audit_event(
            db,
            actor_type="user",
            actor_id=user.id,
            event_type=f"approval.{approval_decision}",
            payload={
                "approval_id": approval.id,
                "action_hash": approval.action_hash,
                "batch": True,
                "selected": selected,
            },
            project_id=action.project_id,
            environment_id=action.environment_id,
            run_id=run.id,
            action_id=action.id,
        )
    if not queue_run_resume(db, run.id):
        db.rollback()
        raise HTTPException(409, "Approval batch could not resume the Agent run")
    db.refresh(run)
    sync_approval_message(db, run)
    db.commit()
    db.refresh(run)
    for approval, action in rows:
        db.refresh(approval)
        db.refresh(action)
    return {
        "approvals": [approval_out(approval, action) for approval, action in rows],
        "run_summary": run_out(run),
    }


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, "approved", db, user)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, payload: ApprovalDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide(approval_id, payload, "rejected", db, user)


@router.post("/agent-runs/{run_id}/approvals/approve")
def approve_batch(run_id: str, payload: ApprovalBatchDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide_batch(run_id, payload, "approved", db, user)


@router.post("/agent-runs/{run_id}/approvals/reject")
def reject_batch(run_id: str, payload: ApprovalBatchDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return decide_batch(run_id, payload, "rejected", db, user)
