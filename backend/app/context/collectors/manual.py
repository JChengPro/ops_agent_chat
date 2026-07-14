from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context.service import upsert_entity, upsert_relationship
from app.models.context import CollectorRun, ContextSource
from app.models.project import Environment


def collect_manual_services(db: Session, environment: Environment) -> CollectorRun:
    run = CollectorRun(project_id=environment.project_id, environment_id=environment.id, collector_name="manual", status="running")
    db.add(run)
    db.flush()
    source = db.scalar(
        select(ContextSource).where(
            ContextSource.project_id == environment.project_id,
            ContextSource.environment_id == environment.id,
            ContextSource.source_type == "manual",
        )
    )
    now = datetime.now(timezone.utc)
    if not source:
        source = ContextSource(
            project_id=environment.project_id,
            environment_id=environment.id,
            source_type="manual",
            source_ref="environment.config_json.known_services",
            collector_name="manual",
            collector_version="1",
            status="active",
            last_verified_at=now,
        )
        db.add(source)
        db.flush()
    raw_entities = [{"type": "service", "name": service, "properties": {"source": "manual"}} for service in (environment.config_json.get("known_services") or [])]
    raw_entities.extend(environment.config_json.get("manual_entities") or [])
    entities = {}
    for raw in raw_entities:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        entity = upsert_entity(
            db,
            project_id=environment.project_id,
            environment_id=environment.id,
            source_id=source.id,
            entity_type=str(raw.get("type") or "component"),
            canonical_name=str(raw["name"]),
            display_name=raw.get("display_name"),
            properties=raw.get("properties") or {"source": "manual"},
        )
        entities[entity.canonical_name] = entity
    relationship_count = 0
    for raw in environment.config_json.get("manual_relationships") or []:
        if not isinstance(raw, dict) or raw.get("from") not in entities or raw.get("to") not in entities:
            continue
        upsert_relationship(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, from_entity_id=entities[raw["from"]].id, to_entity_id=entities[raw["to"]].id, relation_type=str(raw.get("type") or "DEPENDS_ON"))
        relationship_count += 1
    run.status = "success"
    run.finished_at = now
    run.summary_json = {"entities": len(entities), "relationships": relationship_count}
    db.flush()
    return run
