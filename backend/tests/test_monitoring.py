import json
from uuid import uuid4

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.evidence.service import record_result
from app.models.action import Action, Approval
from app.models.context import ProjectEntity
from app.models.monitoring import MonitorEvent
from app.models.project import Connection, Environment, Project, ProjectMember
from app.models.user import User
from app.monitoring.service import claim_due_environment, process_environment_monitor
from app.runtime.adapters.base import AdapterResult


class MonitorExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.status_checks = 0

    def execute(self, db, action, capability):
        del capability
        self.calls.append(action.capability_name)
        if action.capability_name == "service.list":
            records = [{"Service": "frontend", "State": "exited", "ExitCode": 0}]
            result = AdapterResult("success", "服务状态已读取", {"records": records, "parse_valid": True, "stdout": json.dumps(records)})
        elif action.capability_name == "service.status":
            self.status_checks += 1
            running = self.status_checks > 1
            records = [{"Service": "frontend", "State": "running" if running else "exited", "ExitCode": 0, "Health": "healthy" if running else ""}]
            result = AdapterResult("success", "服务状态已复核", {"records": records, "parse_valid": True, "stdout": json.dumps(records)})
        elif action.capability_name == "service.start":
            result = AdapterResult("success", "服务已启动", {"stdout": ""})
        else:
            raise AssertionError(f"Unexpected capability: {action.capability_name}")
        evidence = record_result(db, action, "monitor-test", result)
        return {
            "evidence_id": evidence.id,
            "capability": action.capability_name,
            "status": result.status,
            "summary": result.summary,
            "data": result.data,
            "error_code": result.error_code,
        }


def create_monitored_environment(*, policy_profile: str, auto_remediation: bool) -> int:
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"monitor-{suffix}",
            email=f"monitor-{suffix}@example.test",
            password_hash=hash_password("correct-password"),
            role="admin",
        )
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name=f"monitor-{suffix}")
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        connection = Connection(
            owner_id=user.id,
            name=f"connection-{suffix}",
            connection_type="ssh",
            host="127.0.0.1",
            port=22,
            username="opsagent",
            credential_ref="/run/secrets/test_key",
            host_fingerprint="SHA256:test",
        )
        db.add(connection)
        db.flush()
        environment = Environment(
            project_id=project.id,
            name="default",
            runtime_type="docker_compose",
            connection_id=connection.id,
            workdir="/srv/project",
            config_json={"compose_file": "docker-compose.yml", "known_services": ["frontend"]},
            policy_profile=policy_profile,
            monitoring_enabled=True,
            auto_remediation_enabled=auto_remediation,
            is_default=True,
        )
        db.add(environment)
        db.flush()
        db.add(ProjectEntity(
            id=str(uuid4()),
            project_id=project.id,
            environment_id=environment.id,
            entity_type="service",
            canonical_name="frontend",
            display_name="frontend",
            properties_json={},
        ))
        db.commit()
        return environment.id


def test_monitor_detects_stopped_service_and_auto_remediates_in_development():
    environment_id = create_monitored_environment(policy_profile="development", auto_remediation=True)
    executor = MonitorExecutor()
    with SessionLocal() as db:
        assert claim_due_environment(db, 30) == environment_id
        events = process_environment_monitor(db, environment_id, executor=executor)
        assert len(events) == 1
        event = events[0]
        assert event.status == "remediated"
        assert "自动启动并验证正常" in event.summary
        start = db.scalar(select(Action).where(Action.id == event.remediation_action_id))
        assert start and start.capability_name == "service.start" and start.status == "verified"
        assert db.scalar(select(Approval).where(Approval.action_id == start.id)) is None
        assert executor.calls == ["service.list", "service.status", "service.start", "service.status"]
        environment = db.get(Environment, environment_id)
        assert environment.last_monitored_at is not None
        assert environment.next_monitor_at is not None


def test_monitor_only_alerts_in_production_even_when_auto_remediation_is_enabled():
    environment_id = create_monitored_environment(policy_profile="production", auto_remediation=True)
    executor = MonitorExecutor()
    with SessionLocal() as db:
        events = process_environment_monitor(db, environment_id, executor=executor)
        assert len(events) == 1
        assert events[0].status == "open"
        assert events[0].remediation_action_id is None
        assert executor.calls == ["service.list"]
        assert db.scalar(select(Action).where(Action.environment_id == environment_id, Action.capability_name == "service.start")) is None


def test_disabled_monitor_is_not_claimed_or_processed():
    environment_id = create_monitored_environment(policy_profile="development", auto_remediation=True)
    with SessionLocal() as db:
        environment = db.get(Environment, environment_id)
        environment.monitoring_enabled = False
        environment.next_monitor_at = None
        db.commit()
        assert process_environment_monitor(db, environment_id, executor=MonitorExecutor()) == []
        assert db.scalar(select(MonitorEvent).where(MonitorEvent.environment_id == environment_id)) is None
