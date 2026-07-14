from datetime import datetime, timezone
import hashlib
import json

import yaml
from sqlalchemy import select

from app.context.service import upsert_entity, upsert_relationship
from app.models.context import CollectorRun, ContextSource
from app.runtime.transports.ssh import SSHTransport


class ProjectFileCollector:
    name = "project_file"
    version = "1"

    def __init__(self, transport=None): self.transport = transport or SSHTransport()

    def collect(self, db, environment, connection):
        run = CollectorRun(project_id=environment.project_id, environment_id=environment.id, collector_name=self.name, status="running"); db.add(run); db.flush(); totals = {"entities": 0, "relationships": 0, "files": 0}
        try:
            for path in environment.config_json.get("context_files") or []:
                content = self.transport.read_file(connection, environment, str(path)); payload = json.loads(content) if str(path).endswith(".json") else yaml.safe_load(content)
                if not isinstance(payload, dict): raise ValueError(f"Context file {path} must contain an object")
                source = db.scalar(select(ContextSource).where(ContextSource.project_id == environment.project_id, ContextSource.environment_id == environment.id, ContextSource.source_type == "project_file", ContextSource.source_ref == str(path)))
                if not source: source = ContextSource(project_id=environment.project_id, environment_id=environment.id, source_type="project_file", source_ref=str(path), collector_name=self.name, collector_version=self.version); db.add(source); db.flush()
                source.content_hash = hashlib.sha256(content.encode()).hexdigest(); source.last_verified_at = datetime.now(timezone.utc); source.status = "active"
                entities = {}
                for raw in payload.get("entities") or []:
                    if not isinstance(raw, dict) or not raw.get("name"): continue
                    entity = upsert_entity(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, entity_type=str(raw.get("type") or "component"), canonical_name=str(raw["name"]), display_name=raw.get("display_name"), properties=raw.get("properties") or {}); entities[entity.canonical_name] = entity; totals["entities"] += 1
                for raw in payload.get("relationships") or []:
                    if not isinstance(raw, dict) or raw.get("from") not in entities or raw.get("to") not in entities: continue
                    upsert_relationship(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, from_entity_id=entities[raw["from"]].id, to_entity_id=entities[raw["to"]].id, relation_type=str(raw.get("type") or "DEPENDS_ON")); totals["relationships"] += 1
                totals["files"] += 1
            run.status = "success"; run.summary_json = totals
        except Exception as exc: run.status = "failed"; run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc); db.flush(); return run

