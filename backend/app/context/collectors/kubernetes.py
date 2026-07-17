from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select

from app.context.collectors.base import begin_collector_run
from app.context.cancellation import collector_cancelled
from app.context.service import upsert_entity, upsert_relationship
from app.models.context import CollectorRun, ContextSource
from app.runtime.transports.ssh import SSHTransport


class KubernetesCollector:
    name = "kubernetes"
    version = "1"

    def __init__(self, transport=None): self.transport = transport or SSHTransport()

    def collect(self, db, environment, connection, run=None):
        run = begin_collector_run(db, environment, self.name, run)
        try:
            namespace = environment.namespace or "default"
            result = self.transport.execute(connection, environment, ["kubectl", "-n", namespace, "get", "deployments,services", "-o", "json"], cancel_check=lambda: collector_cancelled(run.id))
            if result.status != "success": raise RuntimeError(result.stderr or "kubectl collection failed")
            payload = json.loads(result.stdout); now = datetime.now(timezone.utc); digest = hashlib.sha256(result.stdout.encode()).hexdigest()
            source = _source(db, environment, f"namespace:{namespace}", digest, now)
            deployments, services = {}, {}
            for item in payload.get("items", []):
                kind = str(item.get("kind", "")).lower(); metadata = item.get("metadata") or {}; spec = item.get("spec") or {}; status = item.get("status") or {}; name = metadata.get("name")
                if not name: continue
                entity = upsert_entity(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, entity_type="service" if kind == "service" else "runtime_unit", canonical_name=name, properties={"kind": kind, "labels": metadata.get("labels") or {}, "selector": spec.get("selector") or {}, "replicas": status.get("replicas"), "available_replicas": status.get("availableReplicas")})
                (services if kind == "service" else deployments)[name] = entity
            relationships = 0
            for service_name, service_entity in services.items():
                selector = service_entity.properties_json.get("selector") or {}
                for deployment_entity in deployments.values():
                    labels = deployment_entity.properties_json.get("labels") or {}
                    if selector and all(labels.get(key) == value for key, value in selector.items()):
                        upsert_relationship(db, project_id=environment.project_id, environment_id=environment.id, source_id=source.id, from_entity_id=service_entity.id, to_entity_id=deployment_entity.id, relation_type="ROUTES_TO"); relationships += 1
            run.summary_json = {"entities": len(services) + len(deployments), "relationships": relationships, "namespace": namespace}; run.error_message = None
        except Exception as exc: run.error_message = str(exc)[:2000]
        db.flush(); return run


def _source(db, environment, ref, digest, now):
    row = db.scalar(select(ContextSource).where(ContextSource.project_id == environment.project_id, ContextSource.environment_id == environment.id, ContextSource.source_type == "kubernetes", ContextSource.source_ref == ref))
    if not row: row = ContextSource(project_id=environment.project_id, environment_id=environment.id, source_type="kubernetes", source_ref=ref, collector_name="kubernetes", collector_version="1"); db.add(row); db.flush()
    row.content_hash = digest; row.status = "active"; row.last_verified_at = now; return row
