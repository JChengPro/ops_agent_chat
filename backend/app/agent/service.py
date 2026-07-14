from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.evidence import EvidenceClaim, EvidenceClaimLink, RuntimeEvidence
from app.capabilities.registry import registry
from app.audit.service import append_audit_event


def create_run(db: Session, session: ChatSession, user_id: int, content: str) -> dict:
    user_message = ChatMessage(session_id=session.id, project_id=session.project_id, role="user", content=content)
    db.add(user_message); db.flush()
    if session.title in {"New chat", "新会话"}:
        session.title = content.strip()[:80] or session.title
    run = AgentRun(
        id=str(uuid4()), session_id=session.id, user_message_id=user_message.id, user_id=user_id,
        project_id=session.project_id, environment_id=session.environment_id, status="running", current_step="queued",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run); db.flush()
    append_audit_event(
        db,
        actor_type="user",
        actor_id=user_id,
        event_type="run.created",
        payload={"session_id": session.id},
        project_id=session.project_id,
        environment_id=session.environment_id,
        run_id=run.id,
    )
    db.commit()
    db.refresh(run)
    return {"user_message": message_out(user_message), "run_summary": run_out(run)}


def execute_run(db: Session, agent, run: AgentRun) -> dict:
    session = db.get(ChatSession, run.session_id)
    user_message = db.get(ChatMessage, run.user_message_id)
    if not session or not user_message:
        raise ValueError("Agent run has no session or user message")
    history_rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id, ChatMessage.id != user_message.id).order_by(ChatMessage.created_at.desc()).limit(12)).all()
    initial = {
        "run_id": run.id, "user_id": run.user_id, "session_id": session.id,
        "project_id": session.project_id, "environment_id": session.environment_id,
        "question": user_message.content, "history": [{"role": item.role, "content": item.content} for item in reversed(history_rows)],
    }
    result = agent.graph.invoke(initial, config={"configurable": {"thread_id": run.id}})
    return _persist_result(db, run, result)


def start_run(db: Session, agent, session: ChatSession, user_id: int, content: str) -> dict:
    created = create_run(db, session, user_id, content)
    run = db.get(AgentRun, created["run_summary"]["id"])
    run.current_step = "starting"
    db.commit()
    return execute_run(db, agent, run)


def resume_run(db: Session, agent, run: AgentRun) -> dict:
    result = agent.graph.invoke(Command(resume={"approved": True}), config={"configurable": {"thread_id": run.id}})
    return _persist_result(db, run, result)


def _persist_result(db: Session, run: AgentRun, result: dict) -> dict:
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        approvals = list(db.scalars(select(Approval).join(Action).where(Action.run_id == run.id, Approval.decision == "pending")))
        content = "该请求包含会改变运行状态的动作，需要你核对目标、影响和风险后明确批准。"
        run.status = "waiting_for_approval"
        message_type = "approval"
        approval_payload = [approval_out(item, db.get(Action, item.action_id)) for item in approvals]
    else:
        content = result.get("answer") or "处理已结束，但没有生成可展示的回答。"
        message_type = "text"
        approval_payload = []
        run.status = result.get("status", "completed")
    message = db.get(ChatMessage, run.assistant_message_id) if run.assistant_message_id else None
    evidence_ids = list(db.scalars(select(RuntimeEvidence.id).where(RuntimeEvidence.run_id == run.id).order_by(RuntimeEvidence.created_at)).all())
    metadata = {"run_id": run.id, "run_status": run.status, "approvals": approval_payload, "evidence_ids": evidence_ids}
    if message:
        message.content = content; message.message_type = message_type; message.metadata_json = metadata
    else:
        message = ChatMessage(session_id=run.session_id, project_id=run.project_id, role="assistant", content=content, message_type=message_type, metadata_json=metadata)
        db.add(message); db.flush(); run.assistant_message_id = message.id
    db.flush()
    claim = db.scalar(select(EvidenceClaim).where(EvidenceClaim.message_id == message.id).limit(1))
    if not claim:
        claim = EvidenceClaim(message_id=message.id, claim_text=content[:10000], claim_type="assistant_answer", confidence=1.0 if run.status == "completed" else 0.5)
        db.add(claim); db.flush()
    else:
        claim.claim_text = content[:10000]; claim.confidence = 1.0 if run.status == "completed" else 0.5
    linked = set(db.scalars(select(EvidenceClaimLink.evidence_id).where(EvidenceClaimLink.claim_id == claim.id, EvidenceClaimLink.evidence_id.is_not(None))).all())
    for evidence_id in evidence_ids:
        if evidence_id not in linked:
            db.add(EvidenceClaimLink(claim_id=claim.id, evidence_id=evidence_id))
    append_audit_event(
        db,
        actor_type="agent",
        actor_id=run.id,
        event_type="run.waiting_for_approval" if run.status == "waiting_for_approval" else "run.finished",
        payload={"status": run.status, "message_id": message.id, "evidence_count": len(evidence_ids)},
        project_id=run.project_id,
        environment_id=run.environment_id,
        run_id=run.id,
    )
    db.commit(); db.refresh(message); db.refresh(run)
    return {"assistant_message": message_out(message), "run_summary": run_out(run), "approvals": approval_payload}


def message_out(item: ChatMessage) -> dict:
    return {"id": item.id, "session_id": item.session_id, "project_id": item.project_id, "role": item.role, "content": item.content, "message_type": item.message_type, "metadata_json": item.metadata_json, "created_at": item.created_at}


def run_out(item: AgentRun) -> dict:
    return {"id": item.id, "session_id": item.session_id, "project_id": item.project_id, "environment_id": item.environment_id, "status": item.status, "current_step": item.current_step, "step_count": item.step_count, "request_json": item.request_json, "plan_json": item.plan_json, "error_code": item.error_code, "error_message": item.error_message, "created_at": item.created_at, "completed_at": item.completed_at}


def approval_out(item: Approval, action: Action | None) -> dict:
    return {"id": item.id, "action_id": item.action_id, "action_hash": item.action_hash, "decision": item.decision, "impact_summary": item.impact_summary, "risk_summary": item.risk_summary, "expires_at": item.expires_at.isoformat(), "decided_at": item.decided_at.isoformat() if item.decided_at else None, "action": action_out(action, json_safe=True) if action else None}


def action_out(item: Action, *, json_safe: bool = False) -> dict:
    created_at = item.created_at.isoformat() if json_safe and item.created_at else item.created_at
    definition = registry.get(item.capability_name)
    return {"id": item.id, "run_id": item.run_id, "capability_name": item.capability_name, "capability_version": item.capability_version, "project_id": item.project_id, "environment_id": item.environment_id, "target_json": item.target_json, "arguments_json": item.arguments_json, "purpose": item.purpose, "effect": item.effect, "action_hash": item.action_hash, "status": item.status, "precheck": definition.precheck if definition else None, "verifier": definition.verifier if definition else None, "rollback": definition.rollback if definition else None, "created_at": created_at}
