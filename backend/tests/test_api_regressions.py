import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import ProgrammingError

from app.agent.service import _persist_result, claim_run, create_run, process_claimed_run, recover_expired_runs
from app.api import auth as auth_api
from app.audit.service import append_audit_event, verify_audit_chain
from app.capabilities.registry import registry
from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password, verify_password
from app.llm.gateway import LLMGateway, ModelCallCancelled
from app.llm.schemas import AgentDecision
from app.main import app
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.governance import AgentWorker, AuditEvent
from app.models.project import Connection, Environment, Project, ProjectMember
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


def test_registration_creates_an_isolated_normal_user_and_returns_a_token(client: TestClient):
    suffix = uuid4().hex[:10]
    response = client.post(
        "/api/auth/register",
        json={
            "username": f"New.User-{suffix}",
            "email": f"New.User-{suffix}@Example.Test",
            "password": "secure-pass-123",
            "password_confirmation": "secure-pass-123",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["user"]["username"] == f"new.user-{suffix}"
    assert payload["user"]["email"] == f"new.user-{suffix}@example.test"
    assert payload["user"]["role"] == "user"
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).json()["id"] == payload["user"]["id"]
    assert client.get("/api/projects", headers=headers).json() == []
    assert client.get("/api/chat-sessions", headers=headers).json() == []
    with SessionLocal() as db:
        user = db.get(User, payload["user"]["id"])
        assert user.password_hash != "secure-pass-123"
        assert verify_password("secure-pass-123", user.password_hash)


def test_registration_rejects_case_insensitive_duplicates_and_invalid_passwords(client: TestClient):
    suffix = uuid4().hex[:10]
    username = f"duplicate-{suffix}"
    email = f"duplicate-{suffix}@example.test"
    valid = {
        "username": username,
        "email": email,
        "password": "secure-pass-123",
        "password_confirmation": "secure-pass-123",
    }
    assert client.post("/api/auth/register", json=valid).status_code == 201
    assert client.post("/api/auth/register", json={**valid, "username": username.upper(), "email": f"other-{suffix}@example.test"}).status_code == 409
    assert client.post("/api/auth/register", json={**valid, "username": f"other-{suffix}", "email": email.upper()}).status_code == 409
    mismatch = client.post("/api/auth/register", json={**valid, "username": f"mismatch-{suffix}", "email": f"mismatch-{suffix}@example.test", "password_confirmation": "different-pass-456"})
    assert mismatch.status_code == 422
    assert "secure-pass-123" not in mismatch.text


def test_registration_honors_invite_code_and_disable_switch(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    suffix = uuid4().hex[:10]
    payload = {
        "username": f"invite-{suffix}",
        "email": f"invite-{suffix}@example.test",
        "password": "secure-pass-123",
        "password_confirmation": "secure-pass-123",
    }
    monkeypatch.setattr(auth_api, "get_settings", lambda: SimpleNamespace(registration_enabled=True, registration_invite_code="private-registration-code"))
    assert client.get("/api/auth/registration").json() == {"enabled": True, "invite_code_required": True}
    assert client.post("/api/auth/register", json=payload).status_code == 403
    assert client.post("/api/auth/register", json={**payload, "invite_code": "private-registration-code"}).status_code == 201
    monkeypatch.setattr(auth_api, "get_settings", lambda: SimpleNamespace(registration_enabled=False, registration_invite_code=""))
    assert client.get("/api/auth/registration").json()["enabled"] is False
    assert client.post("/api/auth/register", json={**payload, "username": f"disabled-{suffix}", "email": f"disabled-{suffix}@example.test"}).status_code == 403


def test_login_lockout_and_inactive_user_rejection(client: TestClient):
    user = create_user()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": user.username, "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": user.username, "password": "wrong"}).status_code == 429
    inactive = create_user(active=False)
    assert client.post("/api/auth/login", json={"username": inactive.username, "password": "correct-password"}).status_code == 401


def test_validation_error_never_echoes_login_password(client: TestClient):
    secret = "must-never-appear-in-response"
    response = client.post("/api/auth/login", json={"password": secret})
    assert response.status_code == 422
    assert secret not in response.text


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


def test_production_registration_requires_a_strong_invite_code():
    with pytest.raises(ValueError, match="REGISTRATION_INVITE_CODE"):
        Settings(
            _env_file=None,
            app_env="production",
            APP_SECRET_KEY="a-secure-application-secret-with-32-characters",
            admin_password="non-default-admin-password",
            database_url="postgresql+psycopg://opsagent:secure@db/ops",
            DEEPSEEK_API_KEY="configured-api-key",
            SSH_STRICT_HOST_KEY_CHECKING=True,
            REGISTRATION_ENABLED=True,
            REGISTRATION_INVITE_CODE="",
        )


def test_llm_placeholder_key_is_not_reported_as_configured():
    placeholder = Settings(_env_file=None, DEEPSEEK_API_KEY="replace-with-your-deepseek-api-key")
    configured = Settings(_env_file=None, DEEPSEEK_API_KEY="test-non-placeholder-key")
    assert placeholder.llm_configured is False
    assert configured.llm_configured is True


def test_production_requires_api_key_for_any_provider_label():
    with pytest.raises(ValueError, match="LLM API key"):
        Settings(
            _env_file=None,
            app_env="production",
            APP_SECRET_KEY="a-secure-application-secret-with-32-characters",
            admin_password="non-default-admin-password",
            database_url="postgresql+psycopg://opsagent:secure@db/ops",
            LLM_PROVIDER="openai-compatible",
            DEEPSEEK_API_KEY="",
            SSH_STRICT_HOST_KEY_CHECKING=True,
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
    assert client.delete(f"/api/environments/{second.json()['id']}", headers=headers).status_code == 200
    remaining = next(item for item in environments if item["id"] != second.json()["id"])
    assert client.delete(f"/api/environments/{remaining['id']}", headers=headers).status_code == 409


def test_environment_monitoring_controls_are_independent_and_disabled_by_default(client: TestClient):
    user = create_user(role="admin")
    headers = bearer(user)
    project = client.post(
        "/api/projects",
        headers=headers,
        json={"name": f"monitoring-{uuid4().hex[:8]}"},
    ).json()
    environment = client.get(
        f"/api/projects/{project['id']}/environments",
        headers=headers,
    ).json()[0]
    assert environment["monitoring_enabled"] is False
    assert environment["auto_remediation_enabled"] is False

    enabled = client.patch(
        f"/api/environments/{environment['id']}",
        headers=headers,
        json={"monitoring_enabled": True, "auto_remediation_enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["monitoring_enabled"] is True
    assert enabled.json()["auto_remediation_enabled"] is True

    monitoring_stopped = client.patch(
        f"/api/environments/{environment['id']}",
        headers=headers,
        json={"monitoring_enabled": False},
    )
    assert monitoring_stopped.status_code == 200
    assert monitoring_stopped.json()["monitoring_enabled"] is False
    assert monitoring_stopped.json()["auto_remediation_enabled"] is True


def test_connection_reference_is_redacted_and_cannot_be_deleted_while_in_use(client: TestClient):
    user = create_user(role="admin")
    headers = bearer(user)
    connection = client.post(
        "/api/connections",
        headers=headers,
        json={
            "name": "test-ssh",
            "connection_type": "ssh",
            "host": "host.docker.internal",
            "port": 22,
            "username": "opsagent",
            "credential_ref": "/run/secrets/test_ssh_key",
            "host_fingerprint": "SHA256:test-fingerprint",
        },
    )
    assert connection.status_code == 200
    connection_payload = connection.json()
    assert connection_payload["credential_configured"] is True
    assert connection_payload["host_fingerprint_configured"] is True
    assert "credential_ref" not in connection_payload and "host_fingerprint" not in connection_payload

    project = client.post("/api/projects", headers=headers, json={"name": f"connection-{uuid4().hex[:8]}"}).json()
    environment = client.get(f"/api/projects/{project['id']}/environments", headers=headers).json()[0]
    configured = client.patch(
        f"/api/environments/{environment['id']}",
        headers=headers,
        json={
            "runtime_type": "docker_compose",
            "connection_id": connection_payload["id"],
            "workdir": "/srv/project",
            "config_json": {"compose_file": "docker-compose.yml"},
        },
    )
    assert configured.status_code == 200
    assert client.delete(f"/api/connections/{connection_payload['id']}", headers=headers).status_code == 409
    assert client.patch(
        f"/api/environments/{environment['id']}",
        headers=headers,
        json={"connection_id": None},
    ).status_code == 200
    assert client.delete(f"/api/connections/{connection_payload['id']}", headers=headers).status_code == 200


def test_project_connection_list_is_scoped_to_referenced_connections(client: TestClient):
    user = create_user(role="admin")
    headers = bearer(user)
    first_project = client.post("/api/projects", headers=headers, json={"name": f"first-{uuid4().hex[:8]}"}).json()
    second_project = client.post("/api/projects", headers=headers, json={"name": f"second-{uuid4().hex[:8]}"}).json()
    first_connection = client.post("/api/connections", headers=headers, json={"name": "first-ssh", "host": "host-a", "port": 22, "username": "opsagent", "credential_ref": "/run/secrets/first", "host_fingerprint": "SHA256:first"}).json()
    second_connection = client.post("/api/connections", headers=headers, json={"name": "second-ssh", "host": "host-b", "port": 22, "username": "opsagent", "credential_ref": "/run/secrets/second", "host_fingerprint": "SHA256:second"}).json()
    for project, connection in ((first_project, first_connection), (second_project, second_connection)):
        environment = client.get(f"/api/projects/{project['id']}/environments", headers=headers).json()[0]
        response = client.patch(f"/api/environments/{environment['id']}", headers=headers, json={"runtime_type": "docker_compose", "connection_id": connection["id"], "workdir": "/srv/project", "config_json": {"compose_file": "docker-compose.yml"}})
        assert response.status_code == 200
    scoped = client.get(f"/api/connections?project_id={first_project['id']}", headers=headers)
    assert scoped.status_code == 200
    assert [item["id"] for item in scoped.json()] == [first_connection["id"]]
    assert {item["id"] for item in client.get("/api/connections", headers=headers).json()} == {first_connection["id"], second_connection["id"]}


def test_system_monitor_session_is_not_exposed_as_user_chat(client: TestClient):
    user = create_user(role="admin")
    headers = bearer(user)
    project = client.post("/api/projects", headers=headers, json={"name": f"system-chat-{uuid4().hex[:8]}"}).json()
    environment = client.get(f"/api/projects/{project['id']}/environments", headers=headers).json()[0]
    with SessionLocal() as db:
        db.add(ChatSession(project_id=project["id"], environment_id=environment["id"], user_id=user.id, title="主动巡检", status="system"))
        db.commit()
    response = client.get(f"/api/projects/{project['id']}/chat-sessions", headers=headers)
    assert response.status_code == 200
    assert all(item["status"] != "system" for item in response.json())


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
        action = Action(id=str(uuid4()), run_id=run.id, capability_name="service.restart", capability_version="final-1", capability_definition_hash=registry.definition_hash("service.restart", "final-1"), project_id=project.id, environment_id=environment.id, target_json={"name": "backend"}, arguments_json={"service": "backend"}, resolved_spec_json={}, rollback_spec_json={"kind": "capability", "capability": "service.start"}, effect="change", action_hash="a" * 64, status="waiting_for_approval")
        db.add(action); db.flush()
        approval = Approval(id=str(uuid4()), action_id=action.id, action_hash=action.action_hash, requested_from=owner.id, decision="pending", impact_summary="将重启 backend。", risk_summary="服务可能短暂不可用。", expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        db.add(approval); db.commit()
        approval_id = approval.id
    response = client.get("/api/approvals", headers=bearer(approver))
    assert response.status_code == 200
    assert approval_id in {item["id"] for item in response.json()}


def test_cancelled_run_cannot_be_approved_afterwards(client: TestClient):
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="cancel-approval")
        db.add(session)
        db.flush()
        message = ChatMessage(session_id=session.id, project_id=None, role="user", content="change")
        db.add(message)
        db.flush()
        run = AgentRun(
            id=str(uuid4()),
            session_id=session.id,
            user_message_id=message.id,
            user_id=user.id,
            status="waiting_for_approval",
        )
        db.add(run)
        db.flush()
        action = Action(
            id=str(uuid4()),
            run_id=run.id,
            capability_name="service.restart",
            capability_version="final-1",
            capability_definition_hash=registry.definition_hash("service.restart", "final-1"),
            project_id=None,
            environment_id=None,
            target_json={"name": "backend"},
            arguments_json={"service": "backend"},
            resolved_spec_json={},
            rollback_spec_json={},
            effect="change",
            action_hash="d" * 64,
            status="waiting_for_approval",
        )
        db.add(action)
        db.flush()
        approval = Approval(
            id=str(uuid4()),
            action_id=action.id,
            action_hash=action.action_hash,
            requested_from=user.id,
            decision="pending",
            impact_summary="Change backend state.",
            risk_summary="Backend may be unavailable.",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add(approval)
        db.commit()
        run_id = run.id
        action_id = action.id
        approval_id = approval.id

    headers = bearer(user)
    assert client.post(f"/api/agent-runs/{run_id}/cancel", headers=headers).status_code == 200
    response = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=headers,
        json={"action_hash": "d" * 64},
    )
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).status == "cancelled"
        assert db.get(Action, action_id).status == "cancelled"
        assert db.get(Approval, approval_id).decision == "cancelled"


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


def test_late_worker_exception_does_not_overwrite_lease_expiry_reason():
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="late-worker-error")
        db.add(session)
        db.commit()
        db.refresh(session)
        queued = create_run(db, session, user.id, "slow task")
        run = claim_run(db, "expired-worker", queued["run_summary"]["id"])
        run_id = run.id

    with SessionLocal() as recovery_db:
        recovery_db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.status == "running")
            .values(
                status="failed",
                error_code="WORKER_LEASE_EXPIRED",
                error_message="lease expired",
                completed_at=datetime.now(timezone.utc),
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        recovery_db.commit()

    failing = SimpleNamespace(
        graph=SimpleNamespace(
            invoke=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late worker failure"))
        )
    )
    with SessionLocal() as db:
        result = process_claimed_run(db, failing, db.get(AgentRun, run_id), "expired-worker")
        run = db.get(AgentRun, run_id)
        assert result["run_summary"]["status"] == "failed"
        assert run.error_code == "WORKER_LEASE_EXPIRED"
        assert "没有自动重放任务" in result["assistant_message"]["content"]


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


def test_cancelling_a_running_run_marks_inflight_action_unknown(client: TestClient):
    user = create_user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="cancel-running")
        db.add(session)
        db.commit()
        db.refresh(session)
        queued = create_run(db, session, user.id, "cancel during execution")
        run = db.get(AgentRun, queued["run_summary"]["id"])
        run.status = "running"
        run.lease_owner = "cancel-test-worker"
        action = Action(
            id=str(uuid4()),
            run_id=run.id,
            capability_name="service.status",
            capability_version="final-1",
            capability_definition_hash=registry.definition_hash("service.status", "final-1"),
            project_id=None,
            environment_id=None,
            target_json={},
            arguments_json={"service": "backend"},
            resolved_spec_json={},
            rollback_spec_json={},
            effect="read",
            action_hash="c" * 64,
            status="executing",
            execution_token=str(uuid4()),
            execution_started_at=datetime.now(timezone.utc),
        )
        db.add(action)
        db.commit()
        run_id = run.id
        action_id = action.id

    response = client.post(f"/api/agent-runs/{run_id}/cancel", headers=bearer(user))
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    with SessionLocal() as db:
        assert db.get(Action, action_id).status == "execution_unknown"


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
            capability_definition_hash=registry.definition_hash("service.status", "final-1"),
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


def test_audit_chain_merges_existing_forks_without_mutating_history():
    with SessionLocal() as db:
        anchor = append_audit_event(
            db,
            actor_type="test",
            actor_id="tester",
            event_type="chain.anchor",
            payload={"value": "anchor"},
        )
        db.commit()
        branch_hashes: list[str] = []
        for value in ("left", "right"):
            event_id = str(uuid4())
            payload = {"value": value}
            canonical = json.dumps(
                {
                    "id": event_id,
                    "actor_type": "legacy-test",
                    "actor_id": "legacy-writer",
                    "event_type": "chain.branch",
                    "project_id": None,
                    "environment_id": None,
                    "run_id": None,
                    "action_id": None,
                    "payload": payload,
                    "previous": anchor.event_hash,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            event_hash = hashlib.sha256(canonical.encode()).hexdigest()
            db.add(
                AuditEvent(
                    id=event_id,
                    actor_type="legacy-test",
                    actor_id="legacy-writer",
                    event_type="chain.branch",
                    payload_json=payload,
                    previous_event_hash=anchor.event_hash,
                    parent_event_hashes_json=[],
                    hash_version=1,
                    event_hash=event_hash,
                )
            )
            branch_hashes.append(event_hash)
        db.commit()

        forked = verify_audit_chain(db)
        assert forked["valid"] is False
        assert forked["reason"] == "multiple_heads"
        assert forked["head_count"] == 2

        merged = append_audit_event(
            db,
            actor_type="test",
            actor_id="repairer",
            event_type="chain.continue",
            payload={"value": "new-business-event"},
        )
        db.commit()
        assert merged.hash_version == 2
        assert merged.parent_event_hashes_json == sorted(branch_hashes)
        assert merged.payload_json["_audit_chain_merge"] == {"merged_head_count": 2}
        verified = verify_audit_chain(db)
        assert verified["valid"] is True
        assert verified["head"] == merged.event_hash
        assert verified["merge_events"] >= 1
