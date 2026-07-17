from datetime import datetime, timedelta, timezone
from threading import Event, Thread
import logging
import socket
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.evidence import EvidenceClaim, EvidenceClaimLink, RuntimeEvidence
from app.audit.service import append_audit_event
from app.agent.status import TERMINAL_RUN_STATUSES, cancel_unstarted_actions, mark_executing_actions_unknown
from app.core.config import get_settings
from app.monitoring.diagnostics import finalize_monitor_diagnosis
from app.utils.public_config import public_config

logger = logging.getLogger(__name__)
WORKER_LEASE_EXPIRED_ANSWER = (
    "处理任务的 Worker 心跳已超时。系统为避免重复执行变更，没有自动重放任务，"
    "也没有采用可能晚到的执行结果；请确认目标当前状态后重新发起。"
)


def create_run(db: Session, session: ChatSession, user_id: int, content: str, client_request_id: str | None = None) -> dict:
    if client_request_id:
        lock_key = f"agent-run:{user_id}:{session.id}:{client_request_id}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key})
        existing = db.scalar(
            select(AgentRun).where(
                AgentRun.user_id == user_id,
                AgentRun.session_id == session.id,
                AgentRun.client_request_id == client_request_id,
            )
        )
        if existing:
            user_message = db.get(ChatMessage, existing.user_message_id)
            return {"user_message": message_out(user_message), "run_summary": run_out(existing), "replayed": True}
    user_message = ChatMessage(session_id=session.id, project_id=session.project_id, role="user", content=content)
    db.add(user_message); db.flush()
    if session.title in {"New chat", "新会话"}:
        session.title = content.strip()[:80] or session.title
    run = AgentRun(
        id=str(uuid4()), session_id=session.id, user_message_id=user_message.id, user_id=user_id,
        project_id=session.project_id, environment_id=session.environment_id, client_request_id=client_request_id,
        status="queued", current_step="queued_new",
        started_at=None,
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
    return {"user_message": message_out(user_message), "run_summary": run_out(run), "replayed": False}


def execute_run(db: Session, agent, run: AgentRun) -> dict:
    session = db.get(ChatSession, run.session_id)
    user_message = db.get(ChatMessage, run.user_message_id)
    if not session or not user_message:
        raise ValueError("Agent run has no session or user message")
    control = run.request_json if isinstance(run.request_json, dict) else {}
    execution_mode = str(control.get("execution_mode") or "interactive")
    history_rows = [] if execution_mode == "monitor_diagnosis" else db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id, ChatMessage.id != user_message.id).order_by(ChatMessage.created_at.desc()).limit(12)).all()
    initial = {
        "run_id": run.id, "user_id": run.user_id, "session_id": session.id,
        "project_id": session.project_id, "environment_id": session.environment_id,
        "question": user_message.content, "history": [{"role": item.role, "content": item.content} for item in reversed(history_rows)],
        "execution_mode": execution_mode,
        "read_only": control.get("read_only") is True,
        "monitor_event_id": control.get("monitor_event_id"),
    }
    result = agent.graph.invoke(initial, config=_graph_config(run.id))
    return _persist_result(db, run, result)


def resume_run(db: Session, agent, run: AgentRun) -> dict:
    result = agent.graph.invoke(Command(resume={"approved": True}), config=_graph_config(run.id))
    return _persist_result(db, run, result)


def _graph_config(run_id: str) -> dict:
    # LangGraph requires a finite recursion guard. Runtime cancellation and the
    # wall-clock timeout remain the real safety boundaries; this value is not a
    # product tool-call or reasoning-step limit.
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 1_000_000}


def _persist_result(db: Session, run: AgentRun, result: dict) -> dict:
    db.refresh(run)
    if run.status == "cancelled" or run.cancel_requested_at:
        result = {
            "status": "cancelled",
            "answer": "本次处理已取消。",
            "claims": [{"text": "该任务已被取消，晚到结果未被采用。", "claim_type": "gap", "evidence_ids": [], "confidence": 0.9}],
        }
    elif run.status == "failed" and run.error_code == "WORKER_LEASE_EXPIRED":
        result = {
            "status": "failed",
            "answer": WORKER_LEASE_EXPIRED_ANSWER,
            "claims": [{"text": "Worker 心跳超时，晚到结果未被采用。", "claim_type": "gap", "evidence_ids": [], "confidence": 0.9}],
        }
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
        approval_rows = list(db.scalars(select(Approval).join(Action).where(Action.run_id == run.id).order_by(Approval.created_at)))
        approval_payload = [approval_out(item, db.get(Action, item.action_id)) for item in approval_rows]
        requested_status = result.get("status", "completed")
        if run.status not in TERMINAL_RUN_STATUSES:
            run.status = "cancelled" if run.cancel_requested_at else requested_status
    if run.status in TERMINAL_RUN_STATUSES:
        run.completed_at = run.completed_at or datetime.now(timezone.utc)
    message = db.get(ChatMessage, run.assistant_message_id) if run.assistant_message_id else None
    evidence_rows = list(db.scalars(select(RuntimeEvidence).where(RuntimeEvidence.run_id == run.id).order_by(RuntimeEvidence.created_at)).all())
    evidence_ids = [item.id for item in evidence_rows]
    context_source_ids, experience_item_ids = _available_source_ids(evidence_rows)
    metadata = {"run_id": run.id, "run_status": run.status, "approvals": approval_payload, "evidence_ids": evidence_ids}
    if message:
        message.content = content; message.message_type = message_type; message.metadata_json = metadata
    else:
        message = ChatMessage(session_id=run.session_id, project_id=run.project_id, role="assistant", content=content, message_type=message_type, metadata_json=metadata)
        db.add(message); db.flush(); run.assistant_message_id = message.id
    db.flush()
    _persist_claims(
        db,
        message.id,
        content,
        result.get("claims") or [],
        evidence_ids,
        context_source_ids=context_source_ids,
        experience_item_ids=experience_item_ids,
    )
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
    run.lease_owner = None
    run.lease_expires_at = None
    run.heartbeat_at = datetime.now(timezone.utc)
    db.commit()
    finalize_monitor_diagnosis(db, run.id)
    db.refresh(message); db.refresh(run)
    return {"assistant_message": message_out(message), "run_summary": run_out(run), "approvals": approval_payload}


def _persist_claims(
    db: Session,
    message_id: int,
    content: str,
    drafts: list[dict],
    evidence_ids: list[str],
    *,
    context_source_ids: set[int] | None = None,
    experience_item_ids: set[int] | None = None,
) -> None:
    existing = list(db.scalars(select(EvidenceClaim.id).where(EvidenceClaim.message_id == message_id)))
    if existing:
        db.execute(delete(EvidenceClaimLink).where(EvidenceClaimLink.claim_id.in_(existing)))
        db.execute(delete(EvidenceClaim).where(EvidenceClaim.id.in_(existing)))
    available = set(evidence_ids)
    available_context = context_source_ids or set()
    available_experience = experience_item_ids or set()
    if not drafts:
        drafts = [{
            "text": content[:10000],
            "claim_type": "inference" if available else "general_knowledge",
            "evidence_ids": [],
            "confidence": 0.5 if available else 0.65,
        }]
    confidence_caps = {"fact": 0.95, "inference": 0.75, "recommendation": 0.7, "general_knowledge": 0.7, "gap": 0.9}
    for draft in drafts[:20]:
        claim_type = str(draft.get("claim_type") or "inference")
        refs = list(dict.fromkeys(item for item in draft.get("evidence_ids", []) if item in available))
        context_refs = list(dict.fromkeys(item for item in draft.get("context_source_ids", []) if item in available_context))
        experience_refs = list(dict.fromkeys(item for item in draft.get("experience_item_ids", []) if item in available_experience))
        confidence = min(float(draft.get("confidence", 0.5)), confidence_caps.get(claim_type, 0.6))
        if claim_type == "fact" and not (refs or context_refs or experience_refs):
            claim_type = "inference"
            confidence = min(confidence, 0.5)
        claim = EvidenceClaim(message_id=message_id, claim_text=str(draft.get("text") or "")[:10000], claim_type=claim_type, confidence=max(0.0, confidence))
        db.add(claim); db.flush()
        for evidence_id in refs:
            db.add(EvidenceClaimLink(claim_id=claim.id, evidence_id=evidence_id))
        for source_id in context_refs:
            db.add(EvidenceClaimLink(claim_id=claim.id, context_source_id=source_id))
        for item_id in experience_refs:
            db.add(EvidenceClaimLink(claim_id=claim.id, experience_item_id=item_id))


def _available_source_ids(evidence_rows: list[RuntimeEvidence]) -> tuple[set[int], set[int]]:
    context_ids: set[int] = set()
    experience_ids: set[int] = set()
    for evidence in evidence_rows:
        data = evidence.data_json if isinstance(evidence.data_json, dict) else {}
        if evidence.capability_name in {
            "project.context.get",
            "relationship.dependencies",
            "relationship.impact",
        }:
            source_ids = data.get("source_ids")
            if isinstance(source_ids, list):
                context_ids.update(item for item in source_ids if isinstance(item, int))
        elif evidence.capability_name == "experience.search":
            items = data.get("items")
            if isinstance(items, list):
                experience_ids.update(
                    item["item_id"]
                    for item in items
                    if isinstance(item, dict) and isinstance(item.get("item_id"), int)
                )
    return context_ids, experience_ids


def claim_run(db: Session, worker_id: str, run_id: str | None = None) -> AgentRun | None:
    statement = select(AgentRun).where(AgentRun.status == "queued").order_by(AgentRun.created_at).with_for_update(skip_locked=True).limit(1)
    if run_id:
        statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.status == "queued").with_for_update(skip_locked=True)
    run = db.scalar(statement)
    if not run:
        return None
    queued_step = run.current_step or "queued_new"
    now = datetime.now(timezone.utc)
    run.status = "running"
    run.current_step = "resuming" if queued_step == "queued_resume" else "starting"
    run.started_at = now
    run.lease_owner = worker_id
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=30)
    db.commit()
    db.refresh(run)
    return run


def process_claimed_run(db: Session, agent, run: AgentRun, worker_id: str) -> dict:
    heartbeat = LeaseHeartbeat(run.id, worker_id)
    heartbeat.start()
    try:
        return resume_run(db, agent, run) if run.current_step == "resuming" else execute_run(db, agent, run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run %s failed", run.id)
        db.rollback()
        failed = db.get(AgentRun, run.id)
        if not failed:
            raise
        claimed = db.scalar(
            update(AgentRun)
            .where(
                AgentRun.id == failed.id,
                AgentRun.status == "running",
                AgentRun.lease_owner == worker_id,
            )
            .values(
                status="failed",
                error_code="RUN_EXECUTION_FAILED",
                error_message=str(exc)[:2000],
                completed_at=datetime.now(timezone.utc),
            )
            .returning(AgentRun.id)
        )
        if claimed:
            mark_executing_actions_unknown(db, failed.id)
            cancel_unstarted_actions(db, failed.id)
            db.flush()
        else:
            db.rollback()
        failed = db.get(AgentRun, run.id)
        if failed.status == "cancelled":
            answer = "本次处理已取消。"
        elif failed.status == "failed":
            answer = "本次处理执行失败，系统已记录错误并安全结束任务。"
        else:
            answer = "本次处理已由其他流程结束，当前 Worker 的晚到异常未被采用。"
        return _persist_result(
            db,
            failed,
            {"status": failed.status, "answer": answer},
        )
    finally:
        heartbeat.stop()


def recover_expired_runs(db: Session) -> int:
    now = datetime.now(timezone.utc)
    candidates = list(
        db.scalars(
            select(AgentRun).where(
                AgentRun.status == "running",
                AgentRun.lease_expires_at.is_not(None),
                AgentRun.lease_expires_at < now,
            ).order_by(AgentRun.lease_expires_at).limit(100)
        )
    )
    recovered = 0
    for candidate in candidates:
        claimed = db.scalar(
            update(AgentRun)
            .where(
                AgentRun.id == candidate.id,
                AgentRun.status == "running",
                AgentRun.lease_expires_at.is_not(None),
                AgentRun.lease_expires_at < now,
            )
            .values(
                status="failed",
                error_code="WORKER_LEASE_EXPIRED",
                error_message="Worker heartbeat expired; automatic replay was blocked to prevent duplicate actions",
                completed_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(AgentRun.id)
        )
        if not claimed:
            db.rollback()
            continue
        run = db.get(AgentRun, claimed)
        db.execute(
            update(Action)
            .where(Action.run_id == run.id, Action.status == "executing")
            .values(status="execution_unknown", execution_finished_at=now)
        )
        cancel_unstarted_actions(db, run.id)
        _persist_result(
            db,
            run,
            {
                "status": "failed",
                "answer": WORKER_LEASE_EXPIRED_ANSWER,
                "claims": [{"text": "Worker 心跳超时，任务已安全终止且未自动重放。", "claim_type": "gap", "evidence_ids": [], "confidence": 0.9}],
            },
        )
        recovered += 1
    return recovered


class LeaseHeartbeat:
    def __init__(self, run_id: str, worker_id: str) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.stopped = Event()
        self.thread = Thread(target=self._run, name=f"lease-{run_id[:8]}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        from app.core.database import SessionLocal
        from app.models.governance import AgentWorker

        while not self.stopped.wait(5):
            with SessionLocal() as db:
                run = db.get(AgentRun, self.run_id)
                if not run or run.status != "running" or run.lease_owner != self.worker_id:
                    return
                now = datetime.now(timezone.utc)
                run.heartbeat_at = now
                run.lease_expires_at = now + timedelta(seconds=30)
                worker = db.get(AgentWorker, self.worker_id)
                if worker:
                    worker.status = "running"
                    worker.last_seen_at = now
                db.commit()


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid4().hex[:8]}"


def message_out(item: ChatMessage) -> dict:
    return {"id": item.id, "session_id": item.session_id, "project_id": item.project_id, "role": item.role, "content": item.content, "message_type": item.message_type, "metadata_json": item.metadata_json, "created_at": item.created_at}


def run_out(item: AgentRun) -> dict:
    return {"id": item.id, "session_id": item.session_id, "project_id": item.project_id, "environment_id": item.environment_id, "client_request_id": item.client_request_id, "status": item.status, "current_step": item.current_step, "step_count": item.step_count, "request_json": item.request_json, "plan_json": item.plan_json, "error_code": item.error_code, "error_message": item.error_message, "created_at": item.created_at, "completed_at": item.completed_at, "heartbeat_at": item.heartbeat_at, "lease_expires_at": item.lease_expires_at}


def approval_out(item: Approval, action: Action | None) -> dict:
    return {"id": item.id, "action_id": item.action_id, "action_hash": item.action_hash, "decision": item.decision, "reason_code": item.reason_code, "impact_summary": item.impact_summary, "risk_summary": item.risk_summary, "expires_at": item.expires_at.isoformat(), "decided_at": item.decided_at.isoformat() if item.decided_at else None, "consumed_at": item.consumed_at.isoformat() if item.consumed_at else None, "action": action_out(action, json_safe=True) if action else None}


def action_out(item: Action, *, json_safe: bool = False) -> dict:
    created_at = item.created_at.isoformat() if json_safe and item.created_at else item.created_at
    bindings = (item.resolved_spec_json or {}).get("capability_bindings") or {}

    def bound_name(relation: str) -> str | None:
        binding = bindings.get(relation) if isinstance(bindings, dict) else None
        return str(binding.get("name")) if isinstance(binding, dict) and binding.get("name") else None

    return {"id": item.id, "run_id": item.run_id, "capability_name": item.capability_name, "capability_version": item.capability_version, "capability_definition_hash": item.capability_definition_hash, "risk_level": item.risk_level, "approval_mode": item.approval_mode, "policy_version": item.policy_version, "config_revision": item.config_revision, "project_id": item.project_id, "environment_id": item.environment_id, "target_json": item.target_json, "arguments_json": item.arguments_json, "resolved_spec_json": public_config(item.resolved_spec_json), "rollback_spec_json": public_config(item.rollback_spec_json), "purpose": item.purpose, "effect": item.effect, "action_hash": item.action_hash, "status": item.status, "precheck": bound_name("precheck"), "verifier": bound_name("verifier"), "rollback": bound_name("rollback"), "created_at": created_at}
