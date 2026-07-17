import json
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.service import claim_run, process_claimed_run
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.core.security import hash_password
from app.evidence.service import record_result
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeDecisionProvider
from app.models.action import Action, Approval
from app.models.agent import AgentRun
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


class UnhealthyMonitorExecutor(MonitorExecutor):
    def execute(self, db, action, capability):
        if action.capability_name != "service.list":
            return super().execute(db, action, capability)
        self.calls.append(action.capability_name)
        records = [{"Service": "frontend", "State": "running", "ExitCode": 0, "Health": "unhealthy"}]
        result = AdapterResult(
            "success",
            "服务状态已读取",
            {"records": records, "parse_valid": True, "stdout": json.dumps(records)},
        )
        evidence = record_result(db, action, "monitor-test", result)
        return {
            "evidence_id": evidence.id,
            "capability": action.capability_name,
            "status": result.status,
            "summary": result.summary,
            "data": result.data,
            "error_code": result.error_code,
        }


class FailedVerificationThenRecoveredExecutor(MonitorExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.list_checks = 0

    def execute(self, db, action, capability):
        del capability
        self.calls.append(action.capability_name)
        if action.capability_name == "service.list":
            self.list_checks += 1
            running = self.list_checks > 1
            records = [{
                "Service": "frontend",
                "State": "running" if running else "exited",
                "ExitCode": 0,
                "Health": "healthy" if running else "",
            }]
            result = AdapterResult(
                "success",
                "服务状态已读取",
                {"records": records, "parse_valid": True, "stdout": json.dumps(records)},
            )
        elif action.capability_name == "service.status":
            records = [{"Service": "frontend", "State": "exited", "ExitCode": 0, "Health": ""}]
            result = AdapterResult(
                "success",
                "服务仍未运行",
                {"records": records, "parse_valid": True, "stdout": json.dumps(records)},
            )
        elif action.capability_name == "service.start":
            result = AdapterResult("success", "服务启动命令已执行", {"stdout": ""})
        else:
            raise AssertionError(f"Unexpected capability: {action.capability_name}")
        evidence = record_result(db, action, "monitor-recovery-test", result)
        return {
            "evidence_id": evidence.id,
            "capability": action.capability_name,
            "status": result.status,
            "summary": result.summary,
            "data": result.data,
            "error_code": result.error_code,
        }


class DiagnosisExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, db, action, capability):
        assert capability.effect == "read"
        self.calls.append(action.capability_name)
        if action.capability_name == "service.status":
            records = [{"Service": "frontend", "State": "running", "ExitCode": 0, "Health": "unhealthy"}]
            result = AdapterResult(
                "success",
                "已复核 frontend 状态",
                {"records": records, "parse_valid": True, "stdout": json.dumps(records)},
            )
        elif action.capability_name == "service.logs":
            result = AdapterResult(
                "success",
                "已读取 frontend 最近日志",
                {"stdout": "health check failed: upstream connection refused"},
            )
        else:
            raise AssertionError(f"Unexpected diagnosis capability: {action.capability_name}")
        evidence = record_result(db, action, "monitor-diagnosis-test", result)
        db.flush()
        return {
            "evidence_id": evidence.id,
            "capability": action.capability_name,
            "status": result.status,
            "summary": result.summary,
            "data": result.data,
            "error_code": result.error_code,
            "observed_at": evidence.observed_at.isoformat(),
            "fresh_until": evidence.fresh_until.isoformat() if evidence.fresh_until else None,
        }

    def rollback(self, db, action, capability):
        del db, action, capability
        raise AssertionError("Read-only diagnosis must never roll back a change")

    def finalize(self, db, action, capability):
        del db, action, capability


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


def test_failed_remediation_is_resolved_when_the_next_patrol_observes_recovery():
    environment_id = create_monitored_environment(policy_profile="development", auto_remediation=True)
    executor = FailedVerificationThenRecoveredExecutor()
    with SessionLocal() as db:
        first = process_environment_monitor(db, environment_id, executor=executor)
        assert len(first) == 1
        event_id = first[0].id
        assert first[0].status == "remediation_failed"
        assert "最终状态验证未通过" in first[0].summary

        assert process_environment_monitor(db, environment_id, executor=executor) == []
        db.expire_all()
        recovered = db.get(MonitorEvent, event_id)
        assert recovered.status == "resolved"
        assert recovered.summary == "frontend 当前状态已恢复正常"
        assert recovered.resolved_at is not None


def test_disabled_monitor_is_not_claimed_or_processed():
    environment_id = create_monitored_environment(policy_profile="development", auto_remediation=True)
    with SessionLocal() as db:
        environment = db.get(Environment, environment_id)
        environment.monitoring_enabled = False
        environment.next_monitor_at = None
        db.commit()
        assert process_environment_monitor(db, environment_id, executor=MonitorExecutor()) == []
        assert db.scalar(select(MonitorEvent).where(MonitorEvent.environment_id == environment_id)) is None


def test_critical_event_queues_one_read_only_diagnosis_and_persists_recommendation():
    environment_id = create_monitored_environment(policy_profile="production", auto_remediation=False)
    monitor_executor = UnhealthyMonitorExecutor()

    with SessionLocal() as db:
        first = process_environment_monitor(db, environment_id, executor=monitor_executor)
        assert len(first) == 1
        event = first[0]
        assert event.severity == "critical"
        assert event.status == "open"
        assert event.diagnostic_run_id
        diagnostic_run_id = event.diagnostic_run_id
        run = db.get(AgentRun, diagnostic_run_id)
        assert run and run.status == "queued"
        assert run.request_json["execution_mode"] == "monitor_diagnosis"
        assert run.request_json["read_only"] is True
        assert run.request_json["monitor_event_id"] == event.id

        repeated = process_environment_monitor(db, environment_id, executor=monitor_executor)
        assert len(repeated) == 1
        assert repeated[0].id == event.id
        assert repeated[0].diagnostic_run_id == diagnostic_run_id

    decisions = [
        {
            "decision": "propose_change",
            "request": {
                "goal": "change",
                "scope": "runtime",
                "time_focus": "current",
                "requested_effect": "change",
                "subjects": [],
                "desired_output": "repair",
                "constraints": [],
                "confidence": 0.9,
                "summary": "尝试越权停止服务",
            },
            "tool_calls": [{"capability": "service.stop", "arguments": {"service": "frontend"}, "purpose": "stop"}],
            "answer": None,
            "clarification_question": None,
        },
        {
            "decision": "respond",
            "request": {
                "goal": "investigate",
                "scope": "runtime",
                "time_focus": "current",
                "requested_effect": "read",
                "subjects": [],
                "desired_output": "diagnosis",
                "constraints": ["read-only"],
                "confidence": 0.9,
                "summary": "生成诊断建议",
            },
            "tool_calls": [],
            "answer": "frontend 仍在运行，但健康检查失败。日志显示上游连接被拒绝，建议先核对依赖服务和健康检查地址，再由用户决定是否批准重启。",
            "clarification_question": None,
            "claims": [],
        },
    ]
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        diagnosis_executor = DiagnosisExecutor()
        graph = OpsAgentGraph(
            checkpointer=saver,
            gateway=LLMGateway(FakeDecisionProvider(decisions)),
            executor=diagnosis_executor,
        )
        with SessionLocal() as db:
            claimed = claim_run(db, "monitor-diagnosis-worker", diagnostic_run_id)
            assert claimed and claimed.id == diagnostic_run_id
            result = process_claimed_run(db, graph, claimed, "monitor-diagnosis-worker")
            assert result["run_summary"]["status"] == "completed"
            db.expire_all()
            event = db.scalar(select(MonitorEvent).where(MonitorEvent.diagnostic_run_id == diagnostic_run_id))
            assert event and event.diagnosed_at is not None
            assert "健康检查失败" in event.diagnosis_summary
            assert "用户决定是否批准重启" in event.diagnosis_summary
            actions = list(db.scalars(select(Action).where(Action.run_id == diagnostic_run_id)))
            assert {item.capability_name for item in actions} == {"service.status", "service.logs"}
            assert all(item.effect == "read" for item in actions)
            assert db.scalar(select(Approval).join(Action).where(Action.run_id == diagnostic_run_id)) is None
            assert diagnosis_executor.calls == ["service.status", "service.logs"]
