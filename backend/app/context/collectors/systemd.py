from datetime import datetime, timezone
import hashlib

from sqlalchemy import select

from app.context.service import upsert_entity
from app.models.context import CollectorRun, ContextSource
from app.runtime.transports.ssh import SSHTransport


class SystemdCollector:
    name = "systemd"
    version = "1"

    def __init__(self, transport=None): self.transport = transport or SSHTransport()

    def collect(self, db, environment, connection):
        run = CollectorRun(project_id=environment.project_id, environment_id=environment.id, collector_name=self.name, status="running"); db.add(run); db.flush()
        try:
            result = self.transport.execute(connection, environment, ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend", "--plain"])
            if result.status != "success": raise RuntimeError(result.stderr or "systemd collection failed")
            now = datetime.now(timezone.utc); ref = "systemd:services"
            source = db.scalar(select(ContextSource).where(ContextSource.project_id == environment.project_id, ContextSource.environment_id == environment.id, ContextSource.source_type == "systemd", ContextSource.source_ref == ref))
            if not source: source = ContextSource(project_id=environment.project_id, environment_id=environment.id, source_type="systemd", source_ref=ref, collector_name=self.name, collector_version=self.version); db.add(source); db.flush()
            source.content_hash = hashlib.sha256(result.stdout.encode()).hexdigest(); source.last_verified_at = now; source.status = "active"
            count = 0
            for line in result.stdout.splitlines():
                fields = line.split(None, 4)
                if len(fields) < 4 or not fields[0].endswith(".service"): continue
                upsert_entity(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, entity_type="runtime_unit", canonical_name=fields[0], properties={"load": fields[1], "active": fields[2], "sub": fields[3], "description": fields[4] if len(fields) > 4 else ""}); count += 1
            run.status = "success"; run.summary_json = {"entities": count}
        except Exception as exc: run.status = "failed"; run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc); db.flush(); return run

