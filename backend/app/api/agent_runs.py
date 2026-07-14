from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.service import action_out, execute_run, run_out
from app.audit.service import append_audit_event
from app.api.deps import require_project
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.action import Action, Approval, ToolInvocation
from app.models.agent import AgentRun, AgentStep
from app.models.evidence import RuntimeEvidence
from app.models.user import User

router = APIRouter(tags=["agent-runs"])


def require_run(db: Session, user: User, run_id: str) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if not run or run.user_id != user.id: raise HTTPException(404, "Agent run not found")
    return run


@router.get("/agent-runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return run_out(require_run(db, user, run_id))


@router.post("/agent-runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = require_run(db, user, run_id)
    if run.status in {"completed", "failed", "cancelled"}: return run_out(run)
    run.status = "cancelled"
    for approval, action in db.execute(select(Approval, Action).join(Action).where(Action.run_id == run.id, Approval.decision == "pending")).all():
        approval.decision = "rejected"; approval.comment = "Run cancelled"; action.status = "cancelled"
    append_audit_event(db, actor_type="user", actor_id=user.id, event_type="run.cancelled", payload={"status": "cancelled"}, project_id=run.project_id, environment_id=run.environment_id, run_id=run.id)
    db.commit(); return run_out(run)


@router.post("/agent-runs/{run_id}/execute")
def execute_queued_run(run_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_run(db, user, run_id)
    claimed = db.scalar(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.user_id == user.id, AgentRun.status == "running", AgentRun.current_step == "queued")
        .values(current_step="starting")
        .returning(AgentRun.id)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(409, "Agent run is not queued or was already started")
    db.commit()
    return execute_run(db, request.app.state.ops_agent, db.get(AgentRun, run_id))


@router.get("/agent-runs/{run_id}/steps")
def steps(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_run(db, user, run_id)
    return db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)).all()


@router.get("/agent-runs/{run_id}/evidence")
def evidence(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_run(db, user, run_id)
    return db.scalars(select(RuntimeEvidence).where(RuntimeEvidence.run_id == run_id).order_by(RuntimeEvidence.created_at)).all()


@router.get("/agent-runs/{run_id}/actions")
def actions(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_run(db, user, run_id)
    return [action_out(item) for item in db.scalars(select(Action).where(Action.run_id == run_id).order_by(Action.created_at)).all()]


@router.get("/actions/{action_id}")
def get_action(action_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Action, action_id)
    if not row: raise HTTPException(404, "Action not found")
    require_run(db, user, row.run_id); return action_out(row)


@router.get("/tool-invocations/{invocation_id}")
def invocation(invocation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ToolInvocation, invocation_id)
    if not row: raise HTTPException(404, "Tool invocation not found")
    require_run(db, user, row.run_id); return row


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(RuntimeEvidence, evidence_id)
    if not row: raise HTTPException(404, "Evidence not found")
    require_run(db, user, row.run_id); return row


@router.get("/projects/{project_id}/agent-runs")
def project_runs(project_id: int, session_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project(db, user, project_id)
    statement = select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.user_id == user.id)
    if session_id: statement = statement.where(AgentRun.session_id == session_id)
    return [run_out(item) for item in db.scalars(statement.order_by(AgentRun.created_at.desc()).limit(100)).all()]


@router.get("/agent-runs")
def general_runs(session_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    statement = select(AgentRun).where(AgentRun.project_id.is_(None), AgentRun.user_id == user.id)
    if session_id:
        statement = statement.where(AgentRun.session_id == session_id)
    return [run_out(item) for item in db.scalars(statement.order_by(AgentRun.created_at.desc()).limit(100)).all()]
