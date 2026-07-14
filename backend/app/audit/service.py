import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.governance import AuditEvent


def append_audit_event(
    db: Session,
    *,
    actor_type: str,
    actor_id: str | int,
    event_type: str,
    payload: dict[str, Any],
    project_id: int | None = None,
    environment_id: int | None = None,
    run_id: str | None = None,
    action_id: str | None = None,
) -> AuditEvent:
    # Serialize the global hash chain inside the current transaction.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 684523901})
    previous = db.scalar(select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1))
    previous_hash = previous.event_hash if previous else None
    event_id = str(uuid4())
    canonical = json.dumps(
        {
            "id": event_id,
            "actor_type": actor_type,
            "actor_id": str(actor_id),
            "event_type": event_type,
            "project_id": project_id,
            "environment_id": environment_id,
            "run_id": run_id,
            "action_id": action_id,
            "payload": payload,
            "previous": previous_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    row = AuditEvent(
        id=event_id,
        actor_type=actor_type,
        actor_id=str(actor_id),
        event_type=event_type,
        project_id=project_id,
        environment_id=environment_id,
        run_id=run_id,
        action_id=action_id,
        payload_json=payload,
        previous_event_hash=previous_hash,
        event_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    db.add(row)
    return row
