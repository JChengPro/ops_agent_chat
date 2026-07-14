from datetime import datetime, timezone
import hashlib
import re

from sqlalchemy import select

from app.context.service import upsert_entity, upsert_relationship
from app.models.context import CollectorRun, ContextSource, ProjectEntity
from app.runtime.transports.ssh import SSHTransport


class NginxCollector:
    name = "nginx"
    version = "1"

    def __init__(self, transport=None): self.transport = transport or SSHTransport()

    def collect(self, db, environment, connection):
        run = CollectorRun(project_id=environment.project_id, environment_id=environment.id, collector_name=self.name, status="running"); db.add(run); db.flush(); entity_count = relationship_count = 0
        try:
            services = {item.canonical_name: item for item in db.scalars(select(ProjectEntity).where(ProjectEntity.project_id == environment.project_id, ProjectEntity.environment_id == environment.id, ProjectEntity.is_active.is_(True))).all()}
            for path in environment.config_json.get("nginx_config_files") or []:
                content = self.transport.read_file(connection, environment, str(path)); source = db.scalar(select(ContextSource).where(ContextSource.project_id == environment.project_id, ContextSource.environment_id == environment.id, ContextSource.source_type == "nginx", ContextSource.source_ref == str(path)))
                if not source: source = ContextSource(project_id=environment.project_id, environment_id=environment.id, source_type="nginx", source_ref=str(path), collector_name=self.name, collector_version=self.version); db.add(source); db.flush()
                source.content_hash = hashlib.sha256(content.encode()).hexdigest(); source.last_verified_at = datetime.now(timezone.utc); source.status = "active"
                server_names = re.findall(r"\bserver_name\s+([^;]+);", _without_comments(content)) or [str(path)]
                upstreams = re.findall(r"\bproxy_pass\s+https?://([^/;:$]+)", _without_comments(content))
                for server_name in server_names:
                    for name in server_name.split():
                        entry = upsert_entity(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, entity_type="entrypoint", canonical_name=name, properties={"config_file": str(path)}); entity_count += 1
                        for upstream in upstreams:
                            target = services.get(upstream)
                            if target:
                                upsert_relationship(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, from_entity_id=entry.id, to_entity_id=target.id, relation_type="ROUTES_TO"); relationship_count += 1
            run.status = "success"; run.summary_json = {"entities": entity_count, "relationships": relationship_count}
        except Exception as exc: run.status = "failed"; run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc); db.flush(); return run


def _without_comments(content: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in content.splitlines())

