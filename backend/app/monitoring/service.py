import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent.graph import resolve_action_spec
from app.audit.service import append_audit_event
from app.capabilities.registry import registry
from app.models.action import Action, PolicyDecision
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.monitoring import MonitorEvent
from app.models.project import Connection, Environment, Project
from app.monitoring.diagnostics import queue_critical_diagnosis
from app.policy.action_hash import action_snapshot, compute_action_hash
from app.policy.engine import PolicyEngine
from app.runtime.executor import RuntimeExecutor
from app.runtime.verification import STOPPED_STATES, parse_json_records, verification_satisfied


logger = logging.getLogger("ops-agent-monitor")
ACTIVE_EVENT_STATUSES = {"open", "remediating", "remediation_failed"}


def claim_due_environment(db: Session, interval_seconds: int) -> int | None:
    now = datetime.now(timezone.utc)
    environment = db.scalar(
        select(Environment)
        .where(
            Environment.is_active.is_(True),
            Environment.monitoring_enabled.is_(True),
            or_(Environment.next_monitor_at.is_(None), Environment.next_monitor_at <= now),
        )
        .order_by(Environment.next_monitor_at.asc().nullsfirst(), Environment.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not environment:
        return None
    environment.next_monitor_at = now + timedelta(seconds=interval_seconds)
    db.commit()
    return environment.id


def process_environment_monitor(
    db: Session,
    environment_id: int,
    *,
    executor: RuntimeExecutor | None = None,
) -> list[MonitorEvent]:
    environment = db.get(Environment, environment_id)
    if not environment or not environment.is_active or not environment.monitoring_enabled:
        return []
    project = db.get(Project, environment.project_id)
    if not project or not project.is_active:
        return []
    runtime = executor or RuntimeExecutor(reuse_ssh_connections=True)
    try:
        return _process_environment_monitor(db, environment, project, runtime)
    finally:
        if executor is None:
            runtime.close()


def _process_environment_monitor(
    db: Session,
    environment: Environment,
    project: Project,
    runtime: RuntimeExecutor,
) -> list[MonitorEvent]:
    run = _create_monitor_run(db, project, environment)
    _, observation = _execute_capability(
        db,
        run,
        environment,
        project.owner_id,
        "service.list",
        {},
        runtime,
        purpose="主动巡检当前环境服务状态",
    )
    events: list[MonitorEvent] = []
    if observation.get("status") != "success":
        issue = {
            "service": "__environment__",
            "issue_type": "monitor_check_failed",
            "severity": "critical",
            "summary": f"{environment.name} 环境巡检失败，无法读取服务状态",
            "details": {"error_code": observation.get("error_code"), "result": observation.get("summary")},
        }
        event = _upsert_event(db, run, environment, issue)
        events.append(event)
        queue_critical_diagnosis(db, event, project, environment, session_id=run.session_id)
        environment.last_monitored_at = datetime.now(timezone.utc)
        _finish_monitor_run(db, run, events, failed=True)
        logger.warning("Environment %s monitoring failed: %s", environment.id, observation.get("summary"))
        return events

    issues = detect_service_issues(environment, observation.get("data") or {})
    issue_keys = {(item["service"], item["issue_type"]) for item in issues}
    _resolve_cleared_events(db, environment.id, issue_keys)
    for issue in issues:
        event = _upsert_event(db, run, environment, issue)
        events.append(event)
        queue_critical_diagnosis(db, event, project, environment, session_id=run.session_id)
        if _eligible_for_automatic_start(environment, issue, event):
            _attempt_automatic_start(db, run, environment, project.owner_id, event, runtime)
    environment.last_monitored_at = datetime.now(timezone.utc)
    _finish_monitor_run(db, run, events, failed=False)
    if events:
        logger.warning("Environment %s monitoring found %s issue(s)", environment.id, len(events))
    return events


def detect_service_issues(environment: Environment, data: dict[str, Any]) -> list[dict[str, Any]]:
    if environment.runtime_type in {"docker_compose", "mixed"}:
        return _docker_issues(environment, data)
    if environment.runtime_type == "kubernetes":
        return _kubernetes_issues(environment, data)
    if environment.runtime_type == "systemd":
        return _systemd_issues(environment, data)
    return [{
        "service": "__environment__",
        "issue_type": "monitor_runtime_unsupported",
        "severity": "warning",
        "summary": f"{environment.name} 环境当前运行时不支持自动服务巡检",
        "details": {"runtime_type": environment.runtime_type},
    }]


def monitor_event_out(item: MonitorEvent, diagnostic_run: AgentRun | None = None) -> dict[str, Any]:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "environment_id": item.environment_id,
        "run_id": item.run_id,
        "service_name": item.service_name,
        "issue_type": item.issue_type,
        "severity": item.severity,
        "status": item.status,
        "summary": item.summary,
        "details_json": item.details_json,
        "occurrence_count": item.occurrence_count,
        "remediation_action_id": item.remediation_action_id,
        "diagnostic_run_id": item.diagnostic_run_id,
        "diagnostic_run_status": diagnostic_run.status if diagnostic_run else None,
        "diagnosis_summary": item.diagnosis_summary,
        "diagnosed_at": item.diagnosed_at,
        "detected_at": item.detected_at,
        "last_seen_at": item.last_seen_at,
        "resolved_at": item.resolved_at,
    }


def _create_monitor_run(db: Session, project: Project, environment: Environment) -> AgentRun:
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.project_id == project.id,
            ChatSession.environment_id == environment.id,
            ChatSession.user_id == project.owner_id,
            ChatSession.status == "system",
        ).limit(1)
    )
    if not session:
        session = ChatSession(
            project_id=project.id,
            environment_id=environment.id,
            user_id=project.owner_id,
            title="主动巡检",
            status="system",
        )
        db.add(session)
        db.flush()
    message = ChatMessage(
        session_id=session.id,
        project_id=project.id,
        role="system",
        content=f"巡检 {project.name} / {environment.name}",
        message_type="monitor",
    )
    db.add(message)
    db.flush()
    run = AgentRun(
        id=str(uuid4()),
        session_id=session.id,
        user_message_id=message.id,
        user_id=project.owner_id,
        project_id=project.id,
        environment_id=environment.id,
        client_request_id=f"monitor:{environment.id}:{uuid4()}",
        status="running",
        request_json={"source": "active_monitor", "goal": "investigate", "summary": f"主动巡检 {environment.name} 环境"},
        current_step="monitoring",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{environment.id}",
        event_type="monitor.started",
        payload={"environment": environment.name},
        project_id=project.id,
        environment_id=environment.id,
        run_id=run.id,
    )
    db.commit()
    return run


def _execute_capability(
    db: Session,
    run: AgentRun,
    environment: Environment,
    user_id: int,
    capability_name: str,
    arguments: dict[str, Any],
    executor: RuntimeExecutor,
    *,
    purpose: str,
    automation: dict[str, Any] | None = None,
) -> tuple[Action, dict[str, Any]]:
    definition = registry.get(capability_name)
    if not definition:
        raise ValueError(f"Monitoring capability is not registered: {capability_name}")
    connection = db.get(Connection, environment.connection_id) if environment.connection_id else None
    action_id = str(uuid4())
    validated = definition.validate_arguments(arguments)
    resolved = resolve_action_spec(environment, definition, validated, action_id, connection)
    if automation:
        resolved["automation"] = automation
    action = Action(
        id=action_id,
        run_id=run.id,
        capability_name=definition.name,
        capability_version=definition.version,
        capability_definition_hash=str(registry.definition_hash(definition.name, definition.version)),
        risk_level=definition.risk_level,
        approval_mode=definition.approval_mode,
        policy_version=PolicyEngine.policy_version,
        config_revision=resolved["configuration_revision"],
        project_id=environment.project_id,
        environment_id=environment.id,
        target_json={"name": validated.get("service") or environment.name},
        arguments_json=validated,
        resolved_spec_json=resolved,
        rollback_spec_json={},
        purpose=purpose,
        effect=definition.effect,
        action_hash="",
        status="proposed",
    )
    policy_engine = PolicyEngine()
    policy = (
        policy_engine.evaluate_automatic_remediation(db, action, definition, user_id)
        if automation
        else policy_engine.evaluate(db, action, definition, user_id)
    )
    action.risk_level = policy.risk_level
    action.action_hash = compute_action_hash(action_snapshot(action))
    db.add(action)
    db.flush()
    db.add(PolicyDecision(
        action_id=action.id,
        decision=policy.decision,
        risk_level=policy.risk_level,
        reason_code=policy.reason_code,
        reason=policy.reason,
        matched_policies_json=policy.matched_policies,
        policy_version=policy_engine.policy_version,
    ))
    if policy.decision != "allow":
        action.status = "denied"
        db.commit()
        return action, {
            "action_id": action.id,
            "capability": action.capability_name,
            "status": "denied",
            "summary": policy.reason,
            "error_code": policy.reason_code,
        }
    action.status = "executing"
    action.execution_token = str(uuid4())
    action.execution_started_at = datetime.now(timezone.utc)
    db.commit()
    action = db.get(Action, action.id)
    try:
        observation = executor.execute(db, action, definition)
    except Exception as exc:  # noqa: BLE001
        action.status = "failed"
        action.execution_finished_at = datetime.now(timezone.utc)
        db.commit()
        return action, {
            "action_id": action.id,
            "capability": action.capability_name,
            "status": "failed",
            "summary": "Runtime execution failed",
            "error_code": "monitor_runtime_exception",
            "error": str(exc)[:1000],
        }
    action.status = "succeeded" if observation.get("status") == "success" else "failed"
    action.execution_finished_at = datetime.now(timezone.utc)
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{environment.id}",
        event_type="monitor.action_executed",
        payload={"capability": action.capability_name, "status": action.status},
        project_id=environment.project_id,
        environment_id=environment.id,
        run_id=run.id,
        action_id=action.id,
    )
    db.commit()
    return action, observation


def _attempt_automatic_start(
    db: Session,
    run: AgentRun,
    environment: Environment,
    user_id: int,
    event: MonitorEvent,
    executor: RuntimeExecutor,
) -> None:
    service = event.service_name
    _, precheck = _execute_capability(
        db, run, environment, user_id, "service.status", {"service": service}, executor,
        purpose=f"自动恢复前复核 {service} 状态",
    )
    if not _observation_is_stopped(precheck):
        event.status = "resolved" if precheck.get("status") == "success" else "remediation_failed"
        event.summary = f"{service} 自动恢复前复核未确认服务已停止"
        event.resolved_at = datetime.now(timezone.utc) if event.status == "resolved" else None
        db.commit()
        return
    event.status = "remediating"
    db.commit()
    action, changed = _execute_capability(
        db,
        run,
        environment,
        user_id,
        "service.start",
        {"service": service},
        executor,
        purpose=f"主动巡检自动启动已停止的 {service} 服务",
        automation={
            "source": "active_monitor",
            "service": service,
            "event_id": event.id,
            "precheck_evidence_id": precheck.get("evidence_id"),
        },
    )
    event.remediation_action_id = action.id
    if changed.get("status") != "success":
        event.status = "remediation_failed"
        event.summary = f"检测到 {service} 已停止，自动启动未成功"
        event.details_json = {**(event.details_json or {}), "remediation_result": changed.get("summary")}
        db.commit()
        return
    _, verification = _execute_capability(
        db, run, environment, user_id, "service.status", {"service": service}, executor,
        purpose=f"验证自动启动后的 {service} 状态",
    )
    action = db.get(Action, action.id)
    if verification_satisfied(action, verification):
        action.status = "verified"
        event.status = "remediated"
        event.summary = f"检测到 {service} 已停止，系统已自动启动并验证正常"
        event.resolved_at = datetime.now(timezone.utc)
    else:
        action.status = "verification_failed"
        event.status = "remediation_failed"
        event.summary = f"{service} 已执行自动启动，但最终状态验证未通过"
    event.details_json = {**(event.details_json or {}), "verification": verification.get("summary")}
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{environment.id}",
        event_type="monitor.remediation_finished",
        payload={"event_id": event.id, "service": service, "status": event.status},
        project_id=environment.project_id,
        environment_id=environment.id,
        run_id=run.id,
        action_id=action.id,
    )
    db.commit()


def _upsert_event(db: Session, run: AgentRun, environment: Environment, issue: dict[str, Any]) -> MonitorEvent:
    now = datetime.now(timezone.utc)
    event = db.scalar(
        select(MonitorEvent).where(
            MonitorEvent.environment_id == environment.id,
            MonitorEvent.service_name == issue["service"],
            MonitorEvent.issue_type == issue["issue_type"],
            MonitorEvent.status.in_(ACTIVE_EVENT_STATUSES),
        ).limit(1)
    )
    if event:
        event.run_id = run.id
        event.last_seen_at = now
        event.occurrence_count += 1
        event.summary = issue["summary"]
        event.details_json = issue["details"]
        return event
    event = MonitorEvent(
        id=str(uuid4()),
        project_id=environment.project_id,
        environment_id=environment.id,
        run_id=run.id,
        service_name=issue["service"],
        issue_type=issue["issue_type"],
        severity=issue["severity"],
        status="open",
        summary=issue["summary"],
        details_json=issue["details"],
        detected_at=now,
        last_seen_at=now,
    )
    db.add(event)
    db.flush()
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{environment.id}",
        event_type="monitor.issue_detected",
        payload={"event_id": event.id, "service": event.service_name, "issue_type": event.issue_type},
        project_id=environment.project_id,
        environment_id=environment.id,
        run_id=run.id,
    )
    return event


def _resolve_cleared_events(db: Session, environment_id: int, issue_keys: set[tuple[str, str]]) -> None:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(MonitorEvent).where(
            MonitorEvent.environment_id == environment_id,
            MonitorEvent.status.in_(ACTIVE_EVENT_STATUSES),
        )
    ).all()
    for event in rows:
        if (event.service_name, event.issue_type) not in issue_keys:
            event.status = "resolved"
            event.resolved_at = now
            event.summary = f"{event.service_name} 当前状态已恢复正常"


def _finish_monitor_run(db: Session, run: AgentRun, events: list[MonitorEvent], *, failed: bool) -> None:
    now = datetime.now(timezone.utc)
    status = "failed" if failed else "completed"
    if failed:
        summary = "主动巡检执行失败，已记录告警并等待人工处理。"
    elif not events:
        summary = "主动巡检完成，当前未发现服务异常。"
    else:
        remediated = sum(item.status == "remediated" for item in events)
        summary = f"主动巡检发现 {len(events)} 个问题，其中 {remediated} 个已自动恢复。"
    message = ChatMessage(
        session_id=run.session_id,
        project_id=run.project_id,
        role="assistant",
        content=summary,
        message_type="monitor",
        metadata_json={"run_id": run.id, "run_status": status, "monitor_event_ids": [item.id for item in events]},
    )
    db.add(message)
    db.flush()
    run.assistant_message_id = message.id
    run.status = status
    run.current_step = "monitoring_finished"
    run.step_count = max(1, len(events) + 1)
    run.completed_at = now
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{run.environment_id}",
        event_type="monitor.finished",
        payload={"status": status, "issues": len(events)},
        project_id=run.project_id,
        environment_id=run.environment_id,
        run_id=run.id,
    )
    db.commit()


def _eligible_for_automatic_start(environment: Environment, issue: dict[str, Any], event: MonitorEvent) -> bool:
    return bool(
        environment.monitoring_enabled
        and environment.auto_remediation_enabled
        and environment.policy_profile in {"development", "test"}
        and environment.runtime_type == "docker_compose"
        and issue["issue_type"] == "service_stopped"
        and event.status == "open"
        and not event.remediation_action_id
    )


def _observation_is_stopped(observation: dict[str, Any]) -> bool:
    if observation.get("status") != "success":
        return False
    records = (observation.get("data") or {}).get("records")
    return bool(
        isinstance(records, list)
        and records
        and all(str(item.get("State") or item.get("state") or "").lower() in STOPPED_STATES for item in records)
    )


def _docker_issues(environment: Environment, data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("records")
    valid = data.get("parse_valid") is True
    if not isinstance(records, list) or not valid:
        records, valid = parse_json_records(data.get("stdout"))
    if not valid:
        return [{
            "service": "__environment__",
            "issue_type": "monitor_output_invalid",
            "severity": "critical",
            "summary": f"{environment.name} 环境返回了无法解析的 Docker Compose 状态",
            "details": {},
        }]
    issues: list[dict[str, Any]] = []
    observed: set[str] = set()
    for record in records:
        service = _record_service_name(record)
        if not service:
            continue
        observed.add(service)
        state = str(record.get("State") or record.get("state") or "").lower()
        health = str(record.get("Health") or record.get("health") or "").lower()
        exit_code = _safe_int(record.get("ExitCode") if "ExitCode" in record else record.get("exit_code"))
        details = {"state": state, "health": health or None, "exit_code": exit_code}
        if state in STOPPED_STATES or state != "running":
            issues.append({
                "service": service,
                "issue_type": "service_stopped",
                "severity": "warning",
                "summary": f"检测到 {service} 服务未运行",
                "details": details,
            })
        elif health and health != "healthy":
            issues.append({
                "service": service,
                "issue_type": "service_unhealthy",
                "severity": "critical" if health == "unhealthy" else "warning",
                "summary": f"检测到 {service} 服务健康状态为 {health}",
                "details": details,
            })
    known = {str(item) for item in (environment.config_json or {}).get("known_services", []) if str(item).strip()}
    for service in sorted(known - observed):
        issues.append({
            "service": service,
            "issue_type": "service_missing",
            "severity": "critical",
            "summary": f"预期的 {service} 服务未出现在运行时状态中",
            "details": {"expected": True},
        })
    return issues


def _kubernetes_issues(environment: Environment, data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("stdout")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        payload = {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return [{"service": "__environment__", "issue_type": "monitor_output_invalid", "severity": "critical", "summary": "Kubernetes 巡检结果无法解析", "details": {}}]
    issues = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        spec = item.get("spec") if isinstance(item, dict) else {}
        status = item.get("status") if isinstance(item, dict) else {}
        service = str((metadata or {}).get("name") or "")
        desired = _safe_int((spec or {}).get("replicas")) or 0
        available = _safe_int((status or {}).get("availableReplicas")) or 0
        if service and (desired <= 0 or available < desired):
            issues.append({"service": service, "issue_type": "service_unhealthy", "severity": "critical", "summary": f"{service} Deployment 可用副本为 {available}/{desired}", "details": {"desired": desired, "available": available}})
    return issues


def _systemd_issues(environment: Environment, data: dict[str, Any]) -> list[dict[str, Any]]:
    known = {str(item) for item in (environment.config_json or {}).get("known_services", []) if str(item).strip()}
    if not known:
        return [{"service": "__environment__", "issue_type": "monitor_config_missing", "severity": "warning", "summary": "systemd 主动巡检需要先登记 known_services", "details": {}}]
    states: dict[str, str] = {}
    for line in str(data.get("stdout") or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            states[parts[0]] = parts[2].lower()
    return [
        {"service": service, "issue_type": "service_stopped", "severity": "warning", "summary": f"检测到 {service} 未处于 active 状态", "details": {"state": states.get(service, "missing")}}
        for service in sorted(known)
        if states.get(service) != "active"
    ]


def _record_service_name(record: dict[str, Any]) -> str:
    return str(record.get("Service") or record.get("service") or "").strip()


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
