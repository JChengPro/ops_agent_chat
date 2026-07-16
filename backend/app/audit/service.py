import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session, aliased

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
    db.flush()
    child = aliased(AuditEvent)
    heads = list(
        db.scalars(
            select(AuditEvent)
            .where(~exists(select(child.id).where(child.previous_event_hash == AuditEvent.event_hash)))
            .with_for_update()
            .limit(2)
        )
    )
    if len(heads) > 1:
        raise ValueError("Audit chain has multiple heads and must be repaired before appending")
    if not heads and db.scalar(select(AuditEvent.id).limit(1)):
        raise ValueError("Audit chain has no valid head and must be repaired before appending")
    previous = heads[0] if heads else None
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


def verify_audit_chain(db: Session) -> dict[str, Any]:
    rows = list(db.scalars(select(AuditEvent)))
    by_previous: dict[str | None, list[AuditEvent]] = {}
    for row in rows:
        by_previous.setdefault(row.previous_event_hash, []).append(row)
    previous_hash = None
    checked = 0
    visited: set[str] = set()
    while checked < len(rows):
        children = by_previous.get(previous_hash, [])
        if len(children) != 1:
            return {"valid": False, "checked": checked, "reason": "chain_branch_or_gap", "previous_hash": previous_hash}
        row = children[0]
        if row.id in visited:
            return {"valid": False, "checked": checked, "event_id": row.id, "reason": "cycle_detected"}
        canonical = json.dumps(
            {
                "id": row.id,
                "actor_type": row.actor_type,
                "actor_id": str(row.actor_id),
                "event_type": row.event_type,
                "project_id": row.project_id,
                "environment_id": row.environment_id,
                "run_id": row.run_id,
                "action_id": row.action_id,
                "payload": row.payload_json,
                "previous": previous_hash,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        if row.event_hash != expected:
            return {"valid": False, "checked": checked, "event_id": row.id, "reason": "event_hash_mismatch"}
        previous_hash = row.event_hash
        visited.add(row.id)
        checked += 1
    return {"valid": True, "checked": checked, "head": previous_hash}
