import hashlib
import json
import logging
from collections import defaultdict, deque
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.governance import AuditEvent


logger = logging.getLogger(__name__)
_AUDIT_CHAIN_LOCK_KEY = 684523901


def _parent_hashes(row: AuditEvent) -> list[str]:
    if row.hash_version >= 2:
        parents = row.parent_event_hashes_json
        if not isinstance(parents, list) or not all(isinstance(value, str) and value for value in parents):
            raise ValueError("invalid_parent_hashes")
        return parents
    return [row.previous_event_hash] if row.previous_event_hash else []


def _canonical_event(
    *,
    event_id: str,
    actor_type: str,
    actor_id: str,
    event_type: str,
    project_id: int | None,
    environment_id: int | None,
    run_id: str | None,
    action_id: str | None,
    payload: dict[str, Any],
    previous_hash: str | None,
    parent_hashes: list[str],
    hash_version: int,
) -> str:
    document: dict[str, Any] = {
        "id": event_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "event_type": event_type,
        "project_id": project_id,
        "environment_id": environment_id,
        "run_id": run_id,
        "action_id": action_id,
        "payload": payload,
        "previous": previous_hash,
    }
    if hash_version >= 2:
        document["hash_version"] = hash_version
        document["parents"] = parent_hashes
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _chain_heads(rows: list[AuditEvent]) -> list[AuditEvent]:
    by_hash: dict[str, AuditEvent] = {}
    referenced: set[str] = set()
    for row in rows:
        if row.event_hash in by_hash:
            raise ValueError("Audit chain contains duplicate event hashes")
        by_hash[row.event_hash] = row
    for row in rows:
        try:
            parents = _parent_hashes(row)
        except ValueError as exc:
            raise ValueError(f"Audit event {row.id} has invalid parent hashes") from exc
        missing = [parent for parent in parents if parent not in by_hash]
        if missing:
            raise ValueError(f"Audit event {row.id} references a missing parent")
        referenced.update(parents)
    return [row for row in rows if row.event_hash not in referenced]


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
    # Serialize discovery and append so concurrent transactions cannot create new forks.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_CHAIN_LOCK_KEY})
    db.flush()
    rows = list(db.scalars(select(AuditEvent).with_for_update()))
    heads = _chain_heads(rows)
    if not heads and rows:
        raise ValueError("Audit chain has no valid head and must be repaired before appending")
    parent_hashes = sorted(row.event_hash for row in heads)
    previous_hash = parent_hashes[0] if parent_hashes else None
    stored_payload = dict(payload)
    if len(parent_hashes) > 1:
        stored_payload["_audit_chain_merge"] = {"merged_head_count": len(parent_hashes)}
        logger.warning("Merging %s existing audit-chain heads with an append-only event", len(parent_hashes))
    event_id = str(uuid4())
    canonical = _canonical_event(
        event_id=event_id,
        actor_type=actor_type,
        actor_id=str(actor_id),
        event_type=event_type,
        project_id=project_id,
        environment_id=environment_id,
        run_id=run_id,
        action_id=action_id,
        payload=stored_payload,
        previous_hash=previous_hash,
        parent_hashes=parent_hashes,
        hash_version=2,
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
        payload_json=stored_payload,
        previous_event_hash=previous_hash,
        parent_event_hashes_json=parent_hashes,
        hash_version=2,
        event_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    db.add(row)
    return row


def verify_audit_chain(db: Session) -> dict[str, Any]:
    rows = list(db.scalars(select(AuditEvent)))
    if not rows:
        return {"valid": True, "checked": 0, "head": None, "root_count": 0, "merge_events": 0}

    by_hash: dict[str, AuditEvent] = {}
    for row in rows:
        if row.event_hash in by_hash:
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "duplicate_event_hash"}
        by_hash[row.event_hash] = row

    children_by_hash: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {event_hash: 0 for event_hash in by_hash}
    merge_events = 0
    for row in rows:
        try:
            parents = _parent_hashes(row)
        except ValueError:
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "invalid_parent_hashes"}
        if len(parents) != len(set(parents)) or (row.hash_version >= 2 and parents != sorted(parents)):
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "invalid_parent_hashes"}
        if row.event_hash in parents:
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "self_parent"}
        missing = [parent for parent in parents if parent not in by_hash]
        if missing:
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "missing_parent_hash"}
        if len(parents) > 1:
            merge_events += 1
        canonical = _canonical_event(
            event_id=row.id,
            actor_type=row.actor_type,
            actor_id=str(row.actor_id),
            event_type=row.event_type,
            project_id=row.project_id,
            environment_id=row.environment_id,
            run_id=row.run_id,
            action_id=row.action_id,
            payload=row.payload_json,
            previous_hash=row.previous_event_hash,
            parent_hashes=parents,
            hash_version=row.hash_version,
        )
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        if row.event_hash != expected:
            return {"valid": False, "checked": 0, "event_id": row.id, "reason": "event_hash_mismatch"}
        indegree[row.event_hash] = len(parents)
        for parent in parents:
            children_by_hash[parent].add(row.event_hash)

    roots = deque(event_hash for event_hash, degree in indegree.items() if degree == 0)
    checked = 0
    remaining = dict(indegree)
    while roots:
        current = roots.popleft()
        checked += 1
        for child_hash in children_by_hash.get(current, set()):
            remaining[child_hash] -= 1
            if remaining[child_hash] == 0:
                roots.append(child_hash)
    if checked != len(rows):
        return {"valid": False, "checked": checked, "reason": "cycle_detected"}

    heads = sorted(event_hash for event_hash in by_hash if not children_by_hash.get(event_hash))
    if len(heads) != 1:
        return {
            "valid": False,
            "checked": checked,
            "reason": "multiple_heads" if heads else "no_head",
            "head_count": len(heads),
            "heads": heads,
        }
    root_count = sum(1 for degree in indegree.values() if degree == 0)
    return {
        "valid": True,
        "checked": checked,
        "head": heads[0],
        "root_count": root_count,
        "merge_events": merge_events,
    }
