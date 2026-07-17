from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.agent.service import _available_source_ids, create_run
from app.api.approvals import ApprovalDecision, decide as decide_approval
from app.context.jobs import claim_collector_run, process_collector_run
from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.context import ContextSource
from app.models.evidence import EvidenceClaim, EvidenceClaimLink
from app.models.experience import ExperienceItem
from app.models.project import Environment, Project
from app.models.user import User
from app.policy.action_hash import action_snapshot, compute_action_hash, configuration_revision
from app.runtime.verification import verification_satisfied


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def _user() -> User:
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        user = User(
            username=f"final-{suffix}",
            email=f"final-{suffix}@example.test",
            password_hash=hash_password("test-password"),
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), {"role": user.role, "ver": user.token_version})
    return {"Authorization": f"Bearer {token}"}


def test_action_hash_binds_governance_and_configuration_revision():
    environment = SimpleNamespace(
        id=7,
        project_id=3,
        runtime_type="docker_compose",
        connection_id=11,
        workdir="/srv/project",
        namespace=None,
        config_json={"compose_file": "compose.yml", "known_services": ["api"]},
        policy_profile="production",
    )
    connection = SimpleNamespace(
        id=11,
        connection_type="ssh",
        host="host-a",
        port=22,
        username="ops",
        credential_ref="/run/secrets/key",
        host_fingerprint="SHA256:first",
        config_json={},
    )
    revision = configuration_revision(environment, connection)
    action = Action(
        capability_name="service.restart",
        capability_version="final-1",
        capability_definition_hash="a" * 64,
        risk_level="L2",
        approval_mode="always",
        policy_version="final-1",
        config_revision=revision,
        project_id=3,
        environment_id=7,
        target_json={"name": "api"},
        arguments_json={"service": "api"},
        resolved_spec_json={"runtime_type": "docker_compose"},
        rollback_spec_json={},
        effect="change",
    )
    approved = compute_action_hash(action_snapshot(action))
    action.policy_version = "final-2"
    assert compute_action_hash(action_snapshot(action)) != approved
    action.policy_version = "final-1"
    connection.host = "host-b"
    assert configuration_revision(environment, connection) != revision


def test_claim_source_discovery_ignores_untrusted_runtime_field_names():
    evidence = [
        SimpleNamespace(
            capability_name="service.inspect",
            data_json={"source_id": 91, "item_id": 92, "nested": {"source_ids": [93]}},
        ),
        SimpleNamespace(capability_name="project.context.get", data_json={"source_ids": [11, 12]}),
        SimpleNamespace(capability_name="experience.search", data_json={"items": [{"item_id": 21}]}),
    ]
    assert _available_source_ids(evidence) == ({11, 12}, {21})


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ('{"State":"running","Health":"healthy","ExitCode":0}', True),
        ('{"State":"running","Health":"","ExitCode":0}', True),
        ('{"State":"running","Health":"starting","ExitCode":0}', False),
        ('{"State":"running","Health":"unhealthy","ExitCode":0}', False),
        ('{"State":"exited","Health":"","ExitCode":1}', False),
        ("not-json", False),
        ("", False),
    ],
)
def test_docker_change_verifier_checks_state_health_exit_and_parse(stdout: str, expected: bool):
    action = Action(
        capability_name="service.restart",
        arguments_json={"service": "api"},
        resolved_spec_json={"runtime_type": "docker_compose"},
    )
    observation = {"status": "success", "data": {"stdout": stdout}}
    assert verification_satisfied(action, observation) is expected


def test_scale_verifier_requires_exact_replica_count_and_stop_requires_observed_state():
    action = Action(
        capability_name="service.scale",
        arguments_json={"service": "api", "replicas": 2},
        resolved_spec_json={"runtime_type": "docker_compose"},
    )
    one = '{"State":"running","ExitCode":0}'
    two = one + "\n" + one
    assert not verification_satisfied(action, {"status": "success", "data": {"stdout": one}})
    assert verification_satisfied(action, {"status": "success", "data": {"stdout": two}})
    action.capability_name = "service.stop"
    assert not verification_satisfied(action, {"status": "success", "data": {"stdout": "[]"}})
    assert verification_satisfied(
        action,
        {"status": "success", "data": {"stdout": '{"State":"exited","ExitCode":0}'}},
    )


def test_message_idempotency_key_reuses_run_and_user_message(client: TestClient):
    user = _user()
    headers = _headers(user)
    session = client.post("/api/chat-sessions", headers=headers, json={"title": "idempotent"}).json()
    payload = {"content": "first message", "client_request_id": f"request-{uuid4().hex}"}
    first = client.post(f"/api/chat-sessions/{session['id']}/agent-runs", headers=headers, json=payload)
    second = client.post(
        f"/api/chat-sessions/{session['id']}/agent-runs",
        headers=headers,
        json={**payload, "content": "must not create another message"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["run_summary"]["id"] == second.json()["run_summary"]["id"]
    assert first.json()["replayed"] is False and second.json()["replayed"] is True
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session["id"])) == 1
        assert db.scalar(select(func.count(AgentRun.id)).where(AgentRun.session_id == session["id"])) == 1


def test_concurrent_message_retries_create_one_run_and_one_message():
    user = _user()
    with SessionLocal() as db:
        session = ChatSession(project_id=None, environment_id=None, user_id=user.id, title="concurrent")
        db.add(session)
        db.commit()
        session_id = session.id
    client_request_id = f"concurrent-{uuid4().hex}"

    def submit(content: str) -> tuple[str, bool]:
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            result = create_run(db, session, user.id, content, client_request_id)
            return result["run_summary"]["id"], result["replayed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["first", "retry"]))
    assert len({run_id for run_id, _ in results}) == 1
    assert sorted(replayed for _, replayed in results) == [False, True]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)) == 1
        assert db.scalar(select(func.count(AgentRun.id)).where(AgentRun.session_id == session_id)) == 1


def test_experience_edit_revokes_verified_status_and_delete_archives(client: TestClient):
    user = _user()
    headers = _headers(user)
    project = client.post("/api/projects", headers=headers, json={"name": f"experience-{uuid4().hex[:8]}"}).json()
    created = client.post(
        f"/api/projects/{project['id']}/experience",
        headers=headers,
        json={"title": "Runbook", "content": "API restart procedure", "trust_status": "verified"},
    )
    assert created.status_code == 200 and created.json()["trust_status"] == "verified"
    item_id = created.json()["id"]
    changed = client.patch(
        f"/api/experience/{item_id}",
        headers=headers,
        json={"content": "Changed procedure", "trust_status": "verified"},
    )
    assert changed.status_code == 200 and changed.json()["trust_status"] == "draft"
    searched = client.post(
        f"/api/projects/{project['id']}/experience/search",
        headers=headers,
        json={"query": "Changed procedure"},
    )
    assert searched.status_code == 200 and searched.json()["items"] == []
    deleted = client.delete(f"/api/experience/{item_id}", headers=headers)
    assert deleted.status_code == 200 and deleted.json()["archived"] is True
    with SessionLocal() as db:
        assert db.get(ExperienceItem, item_id).trust_status == "archived"


def test_claim_link_requires_exactly_one_source_and_source_specific_uniqueness():
    user = _user()
    with SessionLocal() as db:
        project = Project(owner_id=user.id, name=f"claims-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        session = ChatSession(project_id=project.id, environment_id=None, user_id=user.id, title="claims")
        db.add(session)
        db.flush()
        message = ChatMessage(session_id=session.id, project_id=project.id, role="assistant", content="fact")
        source = ContextSource(
            project_id=project.id,
            environment_id=None,
            source_type="manual",
            source_ref="test",
            collector_name="test",
        )
        experience = ExperienceItem(
            project_id=project.id,
            title="known issue",
            content="detail",
            trust_status="verified",
            created_by=user.id,
            verified_by=user.id,
            verified_at=datetime.now(timezone.utc),
        )
        db.add_all([message, source, experience])
        db.flush()
        claim = EvidenceClaim(message_id=message.id, claim_text="supported fact", claim_type="fact", confidence=0.9)
        db.add(claim)
        db.commit()
        ids = claim.id, source.id, experience.id

    claim_id, source_id, experience_id = ids
    with SessionLocal() as db:
        db.add(EvidenceClaimLink(claim_id=claim_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(EvidenceClaimLink(claim_id=claim_id, context_source_id=source_id, experience_item_id=experience_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add_all(
            [
                EvidenceClaimLink(claim_id=claim_id, context_source_id=source_id),
                EvidenceClaimLink(claim_id=claim_id, experience_item_id=experience_id),
            ]
        )
        db.commit()
        db.add(EvidenceClaimLink(claim_id=claim_id, context_source_id=source_id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_collector_api_queues_deduplicates_executes_and_cancels(client: TestClient):
    user = _user()
    headers = _headers(user)
    project = client.post("/api/projects", headers=headers, json={"name": f"collector-{uuid4().hex[:8]}"}).json()
    environment = client.get(f"/api/projects/{project['id']}/environments", headers=headers).json()[0]
    first = client.post(f"/api/environments/{environment['id']}/collect-context", headers=headers)
    second = client.post(f"/api/environments/{environment['id']}/collect-context", headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()[0]["id"] == second.json()[0]["id"]
    with SessionLocal() as db:
        run = claim_collector_run(db, "collector-test-worker")
        assert run and run.id == first.json()[0]["id"]
        completed = process_collector_run(db, run, "collector-test-worker")
        assert completed.status == "completed"
    queued = client.post(f"/api/environments/{environment['id']}/collect-context", headers=headers).json()[0]
    cancelled = client.post(f"/api/collector-runs/{queued['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    running_row = client.post(f"/api/environments/{environment['id']}/collect-context", headers=headers).json()[0]
    with SessionLocal() as db:
        running = claim_collector_run(db, "collector-cancel-worker")
        assert running and running.id == running_row["id"]
        requested = client.post(f"/api/collector-runs/{running.id}/cancel", headers=headers)
        assert requested.status_code == 200
        assert requested.json()["status"] == "running"
        assert requested.json()["cancel_requested_at"]
        finalized = process_collector_run(db, running, "collector-cancel-worker")
        assert finalized.status == "cancelled"


def test_rejecting_one_approval_closes_pending_batch_and_queues_terminal_resume(client: TestClient):
    user = _user()
    headers = _headers(user)
    project_payload = client.post("/api/projects", headers=headers, json={"name": f"approval-{uuid4().hex[:8]}"}).json()
    with SessionLocal() as db:
        project = db.get(Project, project_payload["id"])
        environment = db.scalar(select(Environment).where(Environment.project_id == project.id))
        session = ChatSession(project_id=project.id, environment_id=environment.id, user_id=user.id, title="approval")
        db.add(session)
        db.flush()
        message = ChatMessage(session_id=session.id, project_id=project.id, role="user", content="change two services")
        db.add(message)
        db.flush()
        run = AgentRun(
            id=str(uuid4()),
            session_id=session.id,
            user_message_id=message.id,
            user_id=user.id,
            project_id=project.id,
            environment_id=environment.id,
            status="waiting_for_approval",
        )
        db.add(run)
        db.flush()
        approvals = []
        for index in range(2):
            action = Action(
                id=str(uuid4()),
                run_id=run.id,
                capability_name="service.restart",
                capability_version="final-1",
                capability_definition_hash="a" * 64,
                risk_level="L2",
                approval_mode="always",
                policy_version="final-1",
                config_revision="b" * 64,
                project_id=project.id,
                environment_id=environment.id,
                target_json={"name": f"service-{index}"},
                arguments_json={"service": f"service-{index}"},
                resolved_spec_json={},
                rollback_spec_json={},
                effect="change",
                action_hash="",
                status="waiting_for_approval",
            )
            action.action_hash = compute_action_hash(action_snapshot(action))
            db.add(action)
            db.flush()
            approval = Approval(
                id=str(uuid4()),
                action_id=action.id,
                action_hash=action.action_hash,
                requested_from=user.id,
                impact_summary="将变更服务状态。",
                risk_summary="服务可能短暂不可用。",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            db.add(approval)
            approvals.append(approval)
        db.commit()
        approval_id = approvals[0].id
        action_hash = approvals[0].action_hash
        run_id = run.id

    stale = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=headers,
        json={"action_hash": "f" * 64, "comment": "stale page"},
    )
    assert stale.status_code == 409
    with SessionLocal() as db:
        assert set(db.scalars(select(Approval.decision).join(Action).where(Action.run_id == run_id))) == {"pending"}
        assert db.get(AgentRun, run_id).status == "waiting_for_approval"

    response = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=headers,
        json={"action_hash": action_hash, "comment": "not now"},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        decisions = set(db.scalars(select(Approval.decision).join(Action).where(Action.run_id == run_id)))
        assert decisions == {"rejected", "cancelled"}
        run = db.get(AgentRun, run_id)
        assert run.status == "queued" and run.current_step == "queued_resume"


def test_batch_approval_is_atomic_and_resumes_run_once(client: TestClient):
    user = _user()
    headers = _headers(user)
    project_payload = client.post(
        "/api/projects",
        headers=headers,
        json={"name": f"approval-batch-{uuid4().hex[:8]}"},
    ).json()
    with SessionLocal() as db:
        project = db.get(Project, project_payload["id"])
        environment = db.scalar(select(Environment).where(Environment.project_id == project.id))
        session = ChatSession(
            project_id=project.id,
            environment_id=environment.id,
            user_id=user.id,
            title="batch approval",
        )
        db.add(session)
        db.flush()
        message = ChatMessage(
            session_id=session.id,
            project_id=project.id,
            role="user",
            content="restart two services",
        )
        db.add(message)
        db.flush()
        run = AgentRun(
            id=str(uuid4()),
            session_id=session.id,
            user_message_id=message.id,
            user_id=user.id,
            project_id=project.id,
            environment_id=environment.id,
            status="waiting_for_approval",
        )
        db.add(run)
        db.flush()
        approvals = []
        for index in range(2):
            action = Action(
                id=str(uuid4()),
                run_id=run.id,
                capability_name="service.restart",
                capability_version="final-1",
                capability_definition_hash="a" * 64,
                risk_level="L2",
                approval_mode="always",
                policy_version="final-1",
                config_revision="b" * 64,
                project_id=project.id,
                environment_id=environment.id,
                target_json={"name": f"service-{index}"},
                arguments_json={"service": f"service-{index}"},
                resolved_spec_json={},
                rollback_spec_json={},
                effect="change",
                action_hash="",
                status="waiting_for_approval",
            )
            action.action_hash = compute_action_hash(action_snapshot(action))
            db.add(action)
            db.flush()
            approval = Approval(
                id=str(uuid4()),
                action_id=action.id,
                action_hash=action.action_hash,
                requested_from=user.id,
                impact_summary="将重启服务。",
                risk_summary="服务可能短暂不可用。",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            db.add(approval)
            approvals.append({"approval_id": approval.id, "action_hash": approval.action_hash})
        assistant = ChatMessage(
            session_id=session.id,
            project_id=project.id,
            role="assistant",
            content="请确认变更。",
            message_type="approval",
            metadata_json={
                "run_id": run.id,
                "run_status": "waiting_for_approval",
                "approvals": [
                    {"id": item["approval_id"], "action_hash": item["action_hash"], "decision": "pending"}
                    for item in approvals
                ],
            },
        )
        db.add(assistant)
        db.flush()
        run.assistant_message_id = assistant.id
        db.commit()
        run_id, assistant_message_id = run.id, assistant.id

    incomplete = client.post(
        f"/api/agent-runs/{run_id}/approvals/approve",
        headers=headers,
        json={"approvals": approvals[:1]},
    )
    assert incomplete.status_code == 409
    with SessionLocal() as db:
        assert set(db.scalars(select(Approval.decision).join(Action).where(Action.run_id == run_id))) == {"pending"}
        assert db.get(AgentRun, run_id).status == "waiting_for_approval"

    approved = client.post(
        f"/api/agent-runs/{run_id}/approvals/approve",
        headers=headers,
        json={"approvals": approvals},
    )
    assert approved.status_code == 200
    assert {item["decision"] for item in approved.json()["approvals"]} == {"approved"}
    assert approved.json()["run_summary"]["status"] == "queued"
    with SessionLocal() as db:
        assert set(db.scalars(select(Approval.decision).join(Action).where(Action.run_id == run_id))) == {"approved"}
        assert set(db.scalars(select(Action.status).where(Action.run_id == run_id))) == {"approved"}
        run = db.get(AgentRun, run_id)
        assert run.status == "queued" and run.current_step == "queued_resume"
        message = db.get(ChatMessage, assistant_message_id)
        assert message.metadata_json["run_status"] == "queued"
        assert {item["decision"] for item in message.metadata_json["approvals"]} == {"approved"}

    repeated = client.post(
        f"/api/agent-runs/{run_id}/approvals/approve",
        headers=headers,
        json={"approvals": approvals},
    )
    assert repeated.status_code == 409


def test_rejecting_after_sibling_approval_cancels_unconsumed_approved_action(client: TestClient):
    user = _user()
    headers = _headers(user)
    project_payload = client.post("/api/projects", headers=headers, json={"name": f"approval-order-{uuid4().hex[:8]}"}).json()
    with SessionLocal() as db:
        project = db.get(Project, project_payload["id"])
        environment = db.scalar(select(Environment).where(Environment.project_id == project.id))
        session = ChatSession(project_id=project.id, environment_id=environment.id, user_id=user.id, title="approval order")
        db.add(session)
        db.flush()
        message = ChatMessage(session_id=session.id, project_id=project.id, role="user", content="change two services")
        db.add(message)
        db.flush()
        run = AgentRun(
            id=str(uuid4()), session_id=session.id, user_message_id=message.id, user_id=user.id,
            project_id=project.id, environment_id=environment.id, status="waiting_for_approval",
        )
        db.add(run)
        db.flush()
        approvals = []
        for index in range(2):
            action = Action(
                id=str(uuid4()), run_id=run.id, capability_name="service.restart",
                capability_version="final-1", capability_definition_hash="a" * 64,
                risk_level="L2", approval_mode="always", policy_version="final-1",
                config_revision="b" * 64, project_id=project.id, environment_id=environment.id,
                target_json={"name": f"service-{index}"}, arguments_json={"service": f"service-{index}"},
                resolved_spec_json={}, rollback_spec_json={}, effect="change", action_hash="",
                status="waiting_for_approval",
            )
            action.action_hash = compute_action_hash(action_snapshot(action))
            db.add(action)
            db.flush()
            approval = Approval(
                id=str(uuid4()), action_id=action.id, action_hash=action.action_hash,
                requested_from=user.id, impact_summary="将变更服务状态。",
                risk_summary="服务可能短暂不可用。",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            db.add(approval)
            approvals.append((approval.id, approval.action_hash, action.id))
        db.commit()
        run_id = run.id

    first_id, first_hash, first_action_id = approvals[0]
    second_id, second_hash, second_action_id = approvals[1]
    approved = client.post(f"/api/approvals/{first_id}/approve", headers=headers, json={"action_hash": first_hash})
    assert approved.status_code == 200
    rejected = client.post(f"/api/approvals/{second_id}/reject", headers=headers, json={"action_hash": second_hash})
    assert rejected.status_code == 200
    with SessionLocal() as db:
        assert db.get(Approval, first_id).decision == "approved"
        assert db.get(Approval, first_id).consumed_at is None
        assert db.get(Action, first_action_id).status == "cancelled"
        assert db.get(Action, second_action_id).status == "rejected"
        run = db.get(AgentRun, run_id)
        assert run.status == "queued" and run.current_step == "queued_resume"


def test_concurrent_approval_is_consumed_once_and_run_is_queued_once():
    user = _user()
    with SessionLocal() as db:
        project = Project(owner_id=user.id, name=f"approval-race-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        environment = Environment(project_id=project.id, name="default", runtime_type="manual", is_default=True)
        db.add(environment)
        db.flush()
        session = ChatSession(project_id=project.id, environment_id=environment.id, user_id=user.id, title="race")
        db.add(session)
        db.flush()
        message = ChatMessage(session_id=session.id, project_id=project.id, role="user", content="approve once")
        db.add(message)
        db.flush()
        run = AgentRun(
            id=str(uuid4()),
            session_id=session.id,
            user_message_id=message.id,
            user_id=user.id,
            project_id=project.id,
            environment_id=environment.id,
            status="waiting_for_approval",
        )
        db.add(run)
        db.flush()
        action = Action(
            id=str(uuid4()),
            run_id=run.id,
            capability_name="service.restart",
            capability_version="final-1",
            capability_definition_hash="a" * 64,
            risk_level="L2",
            approval_mode="always",
            policy_version="final-1",
            config_revision="b" * 64,
            project_id=project.id,
            environment_id=environment.id,
            target_json={"name": "api"},
            arguments_json={"service": "api"},
            resolved_spec_json={},
            rollback_spec_json={},
            effect="change",
            action_hash="",
            status="waiting_for_approval",
        )
        action.action_hash = compute_action_hash(action_snapshot(action))
        db.add(action)
        db.flush()
        approval = Approval(
            id=str(uuid4()),
            action_id=action.id,
            action_hash=action.action_hash,
            requested_from=user.id,
            impact_summary="将重启 api。",
            risk_summary="服务可能短暂不可用。",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add(approval)
        db.commit()
        approval_id, action_hash, run_id = approval.id, approval.action_hash, run.id

    def submit() -> int:
        with SessionLocal() as db:
            actor = db.get(User, user.id)
            try:
                decide_approval(
                    approval_id,
                    ApprovalDecision(action_hash=action_hash),
                    "approved",
                    db,
                    actor,
                )
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(statuses) == [200, 409]
    with SessionLocal() as db:
        approval = db.get(Approval, approval_id)
        run = db.get(AgentRun, run_id)
        assert approval.decision == "approved"
        assert run.status == "queued" and run.current_step == "queued_resume"
