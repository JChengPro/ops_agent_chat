from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.action import Action, Approval
from app.models.agent import AgentRun


RUN_STATUSES = {"created", "queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
ACTION_STATUSES = {
    "proposed", "ready", "waiting_for_approval", "approved", "executing", "succeeded", "failed",
    "denied", "needs_clarification", "precheck_failed", "precheck_changed", "rejected", "expired",
    "cancelled", "approval_invalid", "verification_failed", "verified", "rolled_back", "rollback_failed",
    "execution_unknown",
}
TERMINAL_ACTION_STATUSES = {
    "succeeded", "failed", "denied", "needs_clarification", "precheck_failed", "precheck_changed",
    "rejected", "expired", "cancelled", "approval_invalid", "verification_failed", "verified",
    "rolled_back", "rollback_failed", "execution_unknown",
}
APPROVAL_DECISIONS = {"pending", "approved", "rejected", "expired", "cancelled", "invalidated"}


def mark_executing_actions_unknown(db: Session, run_id: str) -> int:
    result = db.execute(
        update(Action)
        .where(Action.run_id == run_id, Action.status == "executing")
        .values(status="execution_unknown", execution_finished_at=datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)


def cancel_unstarted_actions(db: Session, run_id: str) -> int:
    result = db.execute(
        update(Action)
        .where(
            Action.run_id == run_id,
            Action.status.in_(["proposed", "ready", "waiting_for_approval", "approved"]),
        )
        .values(status="cancelled", execution_finished_at=datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)


def queue_run_resume(db: Session, run_id: str) -> bool:
    claimed = db.scalar(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "waiting_for_approval")
        .values(
            status="queued",
            current_step="queued_resume",
            started_at=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        .returning(AgentRun.id)
    )
    return bool(claimed)


def close_pending_approval_batch(
    db: Session,
    run_id: str,
    *,
    decision: str,
    reason_code: str,
    comment: str,
    action_status: str,
    decided_by: int | None = None,
    exclude_approval_id: str | None = None,
) -> int:
    if decision not in APPROVAL_DECISIONS - {"pending", "approved"}:
        raise ValueError(f"Unsupported terminal approval decision: {decision}")
    # All approval/cancellation paths lock Run before Approval rows. Keeping one
    # lock order prevents cancel-vs-expiry and cancel-vs-decision deadlocks.
    db.scalar(select(AgentRun.id).where(AgentRun.id == run_id).with_for_update())
    now = datetime.now(timezone.utc)
    statement = select(Approval, Action).join(Action).where(Action.run_id == run_id, Approval.decision == "pending").with_for_update()
    if exclude_approval_id:
        statement = statement.where(Approval.id != exclude_approval_id)
    rows = db.execute(statement).all()
    for approval, action in rows:
        approval.decision = decision
        approval.reason_code = reason_code
        approval.comment = comment
        approval.decided_by = decided_by
        approval.decided_at = now
        action.status = action_status
    approved_actions = list(
        db.scalars(
            select(Action)
            .join(Approval)
            .where(
                Action.run_id == run_id,
                Action.status == "approved",
                Approval.decision == "approved",
                Approval.consumed_at.is_(None),
            )
            .with_for_update()
        )
    )
    for action in approved_actions:
        action.status = action_status
    return len(rows)


def expire_pending_approval_batches(db: Session) -> int:
    now = datetime.now(timezone.utc)
    run_ids = list(
        db.scalars(
            select(Action.run_id)
            .join(Approval)
            .where(Approval.decision == "pending", Approval.expires_at <= now)
            .distinct()
            .limit(100)
        )
    )
    expired = 0
    for run_id in run_ids:
        expired += close_pending_approval_batch(
            db,
            run_id,
            decision="expired",
            reason_code="APPROVAL_EXPIRED",
            comment="Approval validity period expired",
            action_status="expired",
        )
        queue_run_resume(db, run_id)
    if run_ids:
        db.commit()
    return expired
