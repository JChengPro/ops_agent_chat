from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import String, any_, cast, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import Session

from app.models.context import ProjectEntity, ProjectRelationship


def query_project_context(db: Session, project_id: int, environment_id: int, query: str) -> dict[str, Any]:
    words = [word for word in query.lower().split() if len(word) > 1]
    statement = select(ProjectEntity).where(
        ProjectEntity.project_id == project_id,
        ProjectEntity.environment_id == environment_id,
        ProjectEntity.is_active.is_(True),
    )
    if words:
        clauses = []
        for word in words[:8]:
            clauses.extend([ProjectEntity.canonical_name.ilike(f"%{word}%"), ProjectEntity.display_name.ilike(f"%{word}%")])
        statement = statement.where(or_(*clauses))
    entities = list(db.scalars(statement.order_by(ProjectEntity.entity_type, ProjectEntity.canonical_name).limit(40)))
    if not entities:
        entities = list(
            db.scalars(
                select(ProjectEntity)
                .where(
                    ProjectEntity.project_id == project_id,
                    ProjectEntity.environment_id == environment_id,
                    ProjectEntity.is_active.is_(True),
                )
                .order_by(ProjectEntity.entity_type, ProjectEntity.canonical_name)
                .limit(30)
            )
        )
    return {
        "entities": [entity_to_dict(item) for item in entities],
        "source_ids": sorted({item.source_id for item in entities if item.source_id is not None}),
        "source": "project_context",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def query_relationships(
    db: Session,
    project_id: int,
    environment_id: int,
    entity_name: str,
    depth: int,
    *,
    reverse: bool,
) -> dict[str, Any]:
    entities = list(
        db.scalars(
            select(ProjectEntity).where(
                ProjectEntity.project_id == project_id,
                ProjectEntity.environment_id == environment_id,
                ProjectEntity.is_active.is_(True),
            )
        )
    )
    by_id = {item.id: item for item in entities}
    start = next((item for item in entities if item.canonical_name == entity_name or item.display_name == entity_name), None)
    if not start:
        return {"entity": entity_name, "paths": [], "error": "Entity is not registered"}
    relation = ProjectRelationship.__table__
    source_column = relation.c.to_entity_id if reverse else relation.c.from_entity_id
    target_column = relation.c.from_entity_id if reverse else relation.c.to_entity_id
    base = select(
        target_column.label("entity_id"),
        cast(array([literal(start.id, String), target_column]), ARRAY(String(36))).label("entity_path"),
        cast(array([relation.c.relation_type]), ARRAY(String(60))).label("relation_path"),
        literal(1).label("depth"),
        relation.c.source_id.label("source_id"),
    ).where(
        relation.c.project_id == project_id,
        relation.c.environment_id == environment_id,
        relation.c.is_active.is_(True),
        source_column == start.id,
    )
    traversal = base.cte("relationship_traversal", recursive=True)
    recursive = select(
        target_column,
        cast(traversal.c.entity_path + array([target_column]), ARRAY(String(36))),
        cast(traversal.c.relation_path + array([relation.c.relation_type]), ARRAY(String(60))),
        traversal.c.depth + 1,
        relation.c.source_id,
    ).where(
        relation.c.project_id == project_id,
        relation.c.environment_id == environment_id,
        relation.c.is_active.is_(True),
        source_column == traversal.c.entity_id,
        traversal.c.depth < depth,
        ~(target_column == any_(traversal.c.entity_path)),
    )
    traversal = traversal.union_all(recursive)
    paths: list[dict[str, Any]] = []
    source_ids: set[int] = set()
    for entity_path, relation_path, level, source_id in db.execute(
        select(traversal.c.entity_path, traversal.c.relation_path, traversal.c.depth, traversal.c.source_id)
    ).all():
        names = [by_id[entity_id].canonical_name for entity_id in entity_path if entity_id in by_id]
        paths.append({"path": names, "relations": relation_path, "depth": level})
        if source_id is not None:
            source_ids.add(source_id)
    return {
        "entity": entity_name,
        "paths": paths,
        "source_ids": sorted(source_ids),
        "direction": "impact" if reverse else "dependencies",
    }


def upsert_entity(
    db: Session,
    *,
    project_id: int,
    environment_id: int,
    source_id: int,
    entity_type: str,
    canonical_name: str,
    display_name: str | None = None,
    properties: dict[str, Any] | None = None,
) -> ProjectEntity:
    row = db.scalar(
        select(ProjectEntity).where(
            ProjectEntity.project_id == project_id,
            ProjectEntity.environment_id == environment_id,
            ProjectEntity.entity_type == entity_type,
            ProjectEntity.canonical_name == canonical_name,
        )
    )
    now = datetime.now(timezone.utc)
    if row:
        previous_properties = row.properties_json or {}
        incoming_properties = properties or {}
        if row.source_id and row.source_id != source_id and previous_properties != incoming_properties:
            observations = list(previous_properties.get("_source_observations") or [])
            observations.append({"source_id": row.source_id, "properties": {key: value for key, value in previous_properties.items() if key != "_source_observations"}})
            observations.append({"source_id": source_id, "properties": incoming_properties})
            row.properties_json = {**previous_properties, **incoming_properties, "_source_observations": observations[-6:], "_has_source_conflict": True}
            row.confidence = 0.6
        else:
            row.properties_json = incoming_properties
            row.confidence = 1.0
        row.display_name = display_name or canonical_name
        row.source_id = source_id
        row.last_verified_at = now
        row.is_active = True
        return row
    row = ProjectEntity(
        id=str(uuid4()),
        project_id=project_id,
        environment_id=environment_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        display_name=display_name or canonical_name,
        properties_json=properties or {},
        source_id=source_id,
        confidence=1.0,
        last_verified_at=now,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def upsert_relationship(
    db: Session,
    *,
    project_id: int,
    environment_id: int,
    source_id: int,
    from_entity_id: str,
    to_entity_id: str,
    relation_type: str,
) -> ProjectRelationship:
    row = db.scalar(
        select(ProjectRelationship).where(
            ProjectRelationship.from_entity_id == from_entity_id,
            ProjectRelationship.to_entity_id == to_entity_id,
            ProjectRelationship.relation_type == relation_type,
        )
    )
    now = datetime.now(timezone.utc)
    if row:
        row.source_id = source_id
        row.last_verified_at = now
        row.is_active = True
        return row
    row = ProjectRelationship(
        id=str(uuid4()),
        project_id=project_id,
        environment_id=environment_id,
        from_entity_id=from_entity_id,
        to_entity_id=to_entity_id,
        relation_type=relation_type,
        source_id=source_id,
        confidence=1.0,
        last_verified_at=now,
        is_active=True,
    )
    db.add(row)
    return row


def entity_to_dict(item: ProjectEntity) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.entity_type,
        "name": item.canonical_name,
        "display_name": item.display_name,
        "properties": item.properties_json,
        "source_id": item.source_id,
        "confidence": item.confidence,
        "last_verified_at": item.last_verified_at.isoformat() if item.last_verified_at else None,
    }
