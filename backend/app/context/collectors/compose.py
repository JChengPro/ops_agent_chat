from datetime import datetime, timezone
import hashlib
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context.collectors.base import begin_collector_run
from app.context.cancellation import collector_cancelled
from app.context.service import upsert_entity, upsert_relationship
from app.models.context import CollectorRun, ContextSource
from app.models.project import Connection, Environment
from app.runtime.transports.ssh import SSHTransport


class DockerComposeCollector:
    name = "docker_compose"
    version = "1"

    def __init__(self, transport: SSHTransport | None = None) -> None:
        self.transport = transport or SSHTransport()

    def collect(self, db: Session, environment: Environment, connection: Connection, run: CollectorRun | None = None) -> CollectorRun:
        run = begin_collector_run(db, environment, self.name, run)
        compose_file = str(environment.config_json.get("compose_file") or "docker-compose.yml")
        try:
            content = self.transport.read_file(
                connection,
                environment,
                compose_file,
                cancel_check=lambda: collector_cancelled(run.id),
            )
            payload = yaml.safe_load(content) or {}
            services = payload.get("services") or {}
            if not isinstance(services, dict):
                raise ValueError("Compose services must be an object")
            source = db.scalar(
                select(ContextSource).where(
                    ContextSource.project_id == environment.project_id,
                    ContextSource.environment_id == environment.id,
                    ContextSource.source_type == "compose",
                    ContextSource.source_ref == compose_file,
                )
            )
            digest = hashlib.sha256(content.encode()).hexdigest()
            now = datetime.now(timezone.utc)
            if not source:
                source = ContextSource(
                    project_id=environment.project_id,
                    environment_id=environment.id,
                    source_type="compose",
                    source_ref=compose_file,
                    collector_name=self.name,
                    collector_version=self.version,
                    content_hash=digest,
                    status="active",
                    last_verified_at=now,
                )
                db.add(source)
                db.flush()
            else:
                source.content_hash = digest
                source.status = "active"
                source.last_verified_at = now

            entities = {}
            for service_name, raw in services.items():
                config = raw if isinstance(raw, dict) else {}
                properties = {
                    "image": config.get("image"),
                    "ports": config.get("ports") or [],
                    "healthcheck": config.get("healthcheck") or {},
                    "profiles": config.get("profiles") or [],
                }
                entities[service_name] = upsert_entity(
                    db,
                    project_id=environment.project_id,
                    environment_id=environment.id,
                    source_id=source.id,
                    entity_type="service",
                    canonical_name=service_name,
                    properties=properties,
                )
            relationship_count = 0
            for service_name, raw in services.items():
                config = raw if isinstance(raw, dict) else {}
                dependencies = config.get("depends_on") or []
                names = list(dependencies) if isinstance(dependencies, dict) else dependencies
                for dependency in names:
                    if dependency not in entities:
                        continue
                    upsert_relationship(
                        db,
                        project_id=environment.project_id,
                        environment_id=environment.id,
                        source_id=source.id,
                        from_entity_id=entities[service_name].id,
                        to_entity_id=entities[dependency].id,
                        relation_type="DEPENDS_ON",
                    )
                    relationship_count += 1
            run.summary_json = {"entities": len(entities), "relationships": relationship_count, "source": compose_file}
            run.error_message = None
        except Exception as exc:  # noqa: BLE001
            run.error_message = str(exc)
        db.flush()
        return run
