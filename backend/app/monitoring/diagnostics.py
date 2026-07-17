import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import append_audit_event
from app.models.agent import AgentRun
from app.models.chat import ChatMessage, ChatSession
from app.models.monitoring import MonitorEvent
from app.models.project import Environment, Project


def queue_critical_diagnosis(
    db: Session,
    event: MonitorEvent,
    project: Project,
    environment: Environment,
    *,
    session_id: int,
) -> AgentRun | None:
    """Queue one governed read-only diagnosis for a critical active event."""
    if (
        event.severity != "critical"
        or event.status not in {"open", "remediation_failed"}
        or event.diagnostic_run_id
    ):
        return None
    session = db.get(ChatSession, session_id)
    if not session or session.status != "system":
        raise ValueError("Critical monitor diagnosis requires a system session")

    prompt = _diagnostic_prompt(event, project, environment)
    message = ChatMessage(
        session_id=session.id,
        project_id=project.id,
        role="system",
        content=prompt,
        message_type="monitor_diagnosis_request",
        metadata_json={"monitor_event_id": event.id, "read_only": True},
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
        client_request_id=f"monitor-diagnosis:{event.id}",
        status="queued",
        request_json={
            "source": "active_monitor",
            "execution_mode": "monitor_diagnosis",
            "read_only": True,
            "monitor_event_id": event.id,
            "goal": "investigate",
            "summary": f"自动诊断：{event.summary}",
        },
        current_step="queued_new",
    )
    db.add(run)
    db.flush()
    event.diagnostic_run_id = run.id
    append_audit_event(
        db,
        actor_type="monitor",
        actor_id=f"environment:{environment.id}",
        event_type="monitor.diagnosis_queued",
        payload={"event_id": event.id, "diagnostic_run_id": run.id, "read_only": True},
        project_id=project.id,
        environment_id=environment.id,
        run_id=run.id,
    )
    return run


def finalize_monitor_diagnosis(db: Session, run_id: str) -> None:
    """Copy a terminal diagnostic answer onto its event for notification/UI use."""
    event = db.scalar(select(MonitorEvent).where(MonitorEvent.diagnostic_run_id == run_id))
    if not event or event.diagnosed_at is not None:
        return
    run = db.get(AgentRun, run_id)
    if not run or run.status not in {"completed", "failed", "cancelled"}:
        return
    message = db.get(ChatMessage, run.assistant_message_id) if run.assistant_message_id else None
    if message:
        summary = message.content
    elif run.status == "failed":
        summary = "自动只读诊断未能完成。请检查模型、SSH 连接和目标环境配置后手动发起诊断。"
    else:
        summary = "自动只读诊断已取消，未执行任何状态变更。"
    event.diagnosis_summary = summary[:10000]
    event.diagnosed_at = datetime.now(timezone.utc)
    append_audit_event(
        db,
        actor_type="agent",
        actor_id=run.id,
        event_type="monitor.diagnosis_finished",
        payload={"event_id": event.id, "status": run.status, "message_id": run.assistant_message_id},
        project_id=event.project_id,
        environment_id=event.environment_id,
        run_id=run.id,
    )
    db.commit()


def _diagnostic_prompt(event: MonitorEvent, project: Project, environment: Environment) -> str:
    details = json.dumps(event.details_json or {}, ensure_ascii=False, sort_keys=True)[:4000]
    target_instruction = (
        f"目标服务是 {event.service_name}。优先读取该服务的实时状态和有限行数日志。"
        if event.service_name != "__environment__"
        else "问题影响整个环境。优先复核服务清单、主机资源或已登记健康端点；连接失败时说明具体配置缺口。"
    )
    return (
        "这是主动巡检触发的自动只读诊断。只能调用本轮提供的只读 Capability，"
        "不得提出、创建或执行任何状态变更，也不得等待审批。\n\n"
        f"项目：{project.name}\n"
        f"环境：{environment.name}\n"
        f"运行时：{environment.runtime_type}\n"
        f"巡检事件：{event.summary}\n"
        f"问题类型：{event.issue_type}\n"
        f"事件详情：{details}\n\n"
        f"{target_instruction}\n"
        "请先收集足够的实时证据，再用简体中文给出：当前影响、可能原因、仍缺少的证据和建议处理步骤。"
        "建议可以说明用户之后应批准什么操作，但本次诊断不得调用任何变更能力。"
    )
