import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import ProgrammingError

from app.agent.service import _persist_result, claim_run, create_run, process_claimed_run, recover_expired_runs
from app.audit.service import append_audit_event, verify_audit_chain
from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.llm.gateway import LLMGateway, ModelCallCancelled
from app.llm.schemas import AgentDecision
from app.main import app
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.governance import AgentWorker, AuditEvent
from app.models.project import Environment, Project, ProjectMember
from app.models.user import User
from app.services.seed_service import seed_initial_data
from app.version import APP_VERSION


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def create_user(*, active=True, role="user", password="correct-password") -> User:
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        user = User(
            username=f"user-{suffix}", email=f"user-{suffix}@example.test",
            password_hash=hash_password(password), role=role, is_active=active,
        )
        db.add(user); db.commit(); db.refresh(user)
        db.expunge(user)
        return user


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id), {'role': user.role, 'ver': user.token_version})}"}


def test_logout_revokes_existing_token(client: TestClient):
    user = create_user()
    response = client.post("/api/auth/login", json={"username": user.username, "password": "correct-password"})
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_login_lockout_and_inactive_user_rejection(client: TestClient):
    user = create_user()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": user.username, "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": user.username, "password": "wrong"}).status_code == 429
    inactive = create_user(active=False)
    assert client.post("/api/auth/login", json={"username": inactive.username, "password": "correct-password"}).status_code == 401


def test_health_checks_dependencies_and_reports_consistent_version(client: TestClient):
    with SessionLocal() as db:
        db.execute(delete(AgentWorker))
        db.commit()
    assert client.get("/ready").status_code == 503
    with SessionLocal() as db:
        db.add(AgentWorker(id=f"test-worker-{uuid4().hex[:8]}", status="running", last_seen_at=datetime.now(timezone.utc)))
        db.commit()
    live = client.get("/live")
    ready = client.get("/ready")
    assert live.status_code == ready.status_code == 200
    assert live.json()["version"] == ready.json()["version"] == APP_VERSION == "1.1.0"
    assert ready.json()["checks"] == {"database": "ok", "checkpointer": "ok", "agent": "ok", "model": "configured", "worker": "ok"}


def test_production_settings_reject_unsafe_defaults():
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(
            _env_file=None,
            app_env="production",
            APP_SECRET_KEY="change-me",
            admin_password="change-me-before-running",
            database_url="postgresql+psycopg://opsagent:opsagent_password@db/ops",
            DEEPSEEK_API_KEY="",
            SSH_STRICT_HOST_KEY_CHECKING=False,
        )


def test_environment_api_enforces_schema_and_single_default(client: TestClient):
    user = create_user(role="admin")
    headers = bearer(user)
    project = client.post("/api/projects", headers=headers, json={"name": f"project-{uuid4().hex[:8]}"})
    assert project.status_code == 200
    project_id = project.json()["id"]
    too_long = client.post(f"/api/projects/{project_id}/environments", headers=headers, json={"name": "x" * 81})
    assert too_long.status_code == 422
    invalid = client.post(
        f"/api/projects/{project_id}/environments", headers=headers,
        json={"name": "invalid", "runtime_type": "docker_compose", "config_json": {"services": "backend"}},
    )
    assert invalid.status_code == 422
    second = client.post(
        f"/api/projects/{project_id}/environments", headers=headers,
        json={"name": "production", "runtime_type": "docker_compose", "is_default": True, "config_json": {"compose_file": "docker-compose.yml"}},
    )
    assert second.status_code == 200
    environments = client.get(f"/api/projects/{project_id}/environments", headers=headers).json()
    assert sum(item["is_default"] for item in environments) == 1
    switched = client.patch(
        f"/api/environments/{second.json()['id']}",
        headers=headers,
        json={"runtime_type": "manual"},
    )
    assert switched.status_code == 422


def test_approver_membership_is_visible_in_pending_list(client: TestClient):
    approver = create_user()
    with SessionLocal() as db:
        seed_initial_data(db)
        owner = db.scalar(select(User).where(User.role == "admin").limit(1))
        project = db.scalar(select(Project).where(Project.owner_id == owner.id).limit(1))
        environment = db.scalar(select(Environment).where(Environment.project_id == project.id).limit(1))
        db.add(ProjectMember(project_id=project.id, user_id=approver.id, role="approver"))
        session = ChatSession(project_id=project.id, environment_id=environment.id, user_id=owner.id, title="approval-list")
        db.add(session); db.flush()
        message = ChatMessage(session_id=session.id, project_id=project.id, role="user", content="restart")
        db.add(message); db.flush()
        run = AgentRun(id=str(uuid4()), session_id=session.id, user_message_id=message.id, user_id=owner.id, project_id=project.id, environment_id=environment.id, status="waiting_for_approval")
        db.add(run); db.flush()
        action = Action(id=str(uuid4()), run_id=run.id, capability_name="service.restart", capability_version="final-1", project_id=project.id, environment_id=environment.id, target_json={"name": "backend"}, arguments_json={"service": "backend"}, resolved_spec_json={}, rollback_spec_json={"kind": "capability", "capability": "service.start"}, effect="change", action_hash="a" * 64, status="waiting_for_approval")
        db.add(action); db.flush()
        approval = Approval(id=str(uuid4()), action_id=action.id, action_hash=action.action_hash, requested_from=owner.id, decision="pending", impact_summary="将重启 backend。", risk_summary="服务可能短暂不可用。", expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        db.add(approval); db.commit()
        approval_id = approval.id
    response = client.get("/api/approvals", headers=bearer(approver))
    assert response.status_code == 200
    assert approval_id in {item["id"] for item in response.json()}


def test_worker_claim_is_single_owner_and_graph_exception_is_terminal():
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="worker")
        db.add(session); db.commit(); db.refresh(session)
        queued = create_run(db, session, user.id, "hello")
        run_id = queued["run_summary"]["id"]
        claimed = claim_run(db, "worker-a", run_id)
        assert claimed and claimed.status == "running"
    with SessionLocal() as other:
        assert claim_run(other, "worker-b", run_id) is None
    failing = SimpleNamespace(graph=SimpleNamespace(invoke=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure"))))
    with SessionLocal() as db:
        result = process_claimed_run(db, failing, db.get(AgentRun, run_id), "worker-a")
        assert result["run_summary"]["status"] == "failed"
        assert result["assistant_message"]["content"].startswith("本次处理执行失败")


def test_http_execute_compatibility_endpoint_never_runs_the_graph(client: TestClient):
    user = create_user()
    headers = bearer(user)
    session = client.post("/api/chat-sessions", headers=headers, json={"title": "queued-only"})
    assert session.status_code == 200
    queued = client.post(
        f"/api/chat-sessions/{session.json()['id']}/agent-runs",
        headers=headers,
        json={"content": "hello"},
    )
    assert queued.status_code == 202
    run_id = queued.json()["run_summary"]["id"]
    response = client.post(f"/api/agent-runs/{run_id}/execute", headers=headers)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).status == "queued"


def test_cancelling_a_queued_run_creates_a_terminal_assistant_message(client: TestClient):
    user = create_user()
    headers = bearer(user)
    session = client.post("/api/chat-sessions", headers=headers, json={"title": "cancel-queued"}).json()
    queued = client.post(f"/api/chat-sessions/{session['id']}/agent-runs", headers=headers, json={"content": "cancel me"}).json()
    run_id = queued["run_summary"]["id"]
    cancelled = client.post(f"/api/agent-runs/{run_id}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    messages = client.get(f"/api/chat-sessions/{session['id']}/messages", headers=headers).json()
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "本次处理已取消。"


def test_late_result_cannot_overwrite_cancelled_run():
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="cancel")
        db.add(session); db.commit(); db.refresh(session)
        queued = create_run(db, session, user.id, "slow request")
        run = db.get(AgentRun, queued["run_summary"]["id"])
        run.status = "cancelled"
        run.cancel_requested_at = datetime.now(timezone.utc)
        db.commit()
        result = _persist_result(db, run, {"status": "completed", "answer": "late success"})
        assert result["run_summary"]["status"] == "cancelled"
        assert result["assistant_message"]["content"] == "本次处理已取消。"


def test_expired_worker_lease_is_failed_once_with_a_user_visible_result():
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="expired")
        db.add(session); db.commit(); db.refresh(session)
        queued = create_run(db, session, user.id, "expired request")
        run = db.get(AgentRun, queued["run_summary"]["id"])
        run.status = "running"
        run.lease_owner = "dead-worker"
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        action = Action(
            id=str(uuid4()), run_id=run.id, capability_name="service.status", capability_version="final-1",
            project_id=None, environment_id=None, target_json={}, arguments_json={}, resolved_spec_json={},
            rollback_spec_json={}, effect="read", action_hash="b" * 64, status="executing",
        )
        db.add(action)
        db.commit()
        assert recover_expired_runs(db) == 1
        assert recover_expired_runs(db) == 0
        db.refresh(run)
        assert run.status == "failed" and run.error_code == "WORKER_LEASE_EXPIRED"
        db.refresh(action)
        assert action.status == "execution_unknown"
        message = db.get(ChatMessage, run.assistant_message_id)
        assert "没有自动重放任务" in message.content


def test_model_wait_can_be_cancelled_without_accepting_late_result():
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="model-cancel")
        db.add(session); db.commit(); db.refresh(session)
        queued = create_run(db, session, user.id, "slow model")
        run_id = queued["run_summary"]["id"]

        class SlowProvider:
            def decide(self, **kwargs):
                del kwargs
                time.sleep(0.8)
                return AgentDecision.model_validate({
                    "decision": "respond",
                    "request": {"goal": "answer", "scope": "general", "time_focus": "timeless", "requested_effect": "none", "subjects": [], "desired_output": "answer", "constraints": [], "confidence": 0.7, "summary": "slow"},
                    "tool_calls": [], "answer": "late", "claims": [],
                })

        checks = 0
        def cancelled():
            nonlocal checks
            checks += 1
            return checks >= 3

        started = time.monotonic()
        with pytest.raises(ModelCallCancelled):
            LLMGateway(SlowProvider()).decide(db, run_id=run_id, question="slow", history=[], context={}, capabilities=[], evidence=[], cancel_check=cancelled)
        assert time.monotonic() - started < 0.7
        db.rollback()


def test_audit_chain_verifier_detects_and_reports_tampering():
    with SessionLocal() as db:
        first = append_audit_event(db, actor_type="test", actor_id="tester", event_type="chain.test", payload={"value": 1})
        second = append_audit_event(db, actor_type="test", actor_id="tester", event_type="chain.test", payload={"value": 2})
        third = append_audit_event(db, actor_type="test", actor_id="tester", event_type="chain.test", payload={"value": 3})
        db.commit()
        assert verify_audit_chain(db)["valid"] is True

        second_id = second.id
        second.payload_json = {"value": "tampered"}
        with pytest.raises(ProgrammingError, match="audit_events is append-only"):
            db.commit()
        db.rollback()

        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
        db.execute(update(AuditEvent).where(AuditEvent.id == second_id).values(payload_json={"value": "tampered"}))
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only"))
        db.commit()
        invalid = verify_audit_chain(db)
        assert invalid["valid"] is False
        assert invalid["reason"] == "event_hash_mismatch"

        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
        db.execute(update(AuditEvent).where(AuditEvent.id == second_id).values(payload_json={"value": 2}))
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only"))
        db.commit()
        assert verify_audit_chain(db)["valid"] is True
