from app.capabilities.schemas import CapabilityDefinition
from app.context.service import query_project_context, query_relationships
from app.evidence.service import record_result
from app.experience.service import search_experience
from app.models.action import Action
from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult
from app.runtime.adapters.docker import DockerComposeAdapter
from app.runtime.adapters.host import HostAdapter
from app.runtime.adapters.http import HttpAdapter
from app.runtime.adapters.kubernetes import KubernetesAdapter
from app.runtime.adapters.registered import RegisteredConfigAdapter, RegisteredDeploymentAdapter
from app.runtime.adapters.systemd import SystemdAdapter


class RuntimeExecutor:
    def execute(self, db, action: Action, capability: CapabilityDefinition) -> dict:
        environment = db.get(Environment, action.environment_id)
        if not environment:
            result = AdapterResult("failed", "Environment is missing", {}, error="environment_not_found")
            return self._record(db, action, capability.executor, result)
        args = action.arguments_json
        if capability.executor == "context":
            if capability.name == "project.context.get":
                data = query_project_context(db, action.project_id, environment.id, args["query"])
            else:
                data = query_relationships(
                    db,
                    action.project_id,
                    environment.id,
                    args["entity"],
                    args.get("depth", 2),
                    reverse=capability.name == "relationship.impact",
                )
            return self._record(db, action, "context", AdapterResult("success", "Project context retrieved", data))
        if capability.executor == "experience":
            data = search_experience(db, action.project_id, args["query"], args.get("limit", 5))
            return self._record(db, action, "experience", AdapterResult("success", "Verified experience searched", data))
        connection = db.get(Connection, environment.connection_id) if environment.connection_id else None
        if capability.name == "http.health_check":
            result = HttpAdapter().execute(args, environment)
            return self._record(db, action, "http", result)
        if not connection:
            result = AdapterResult("failed", "Runtime connection is not configured", {}, error="connection_not_found")
            return self._record(db, action, "runtime", result)
        if capability.executor == "registered_deployment":
            adapter = RegisteredDeploymentAdapter()
        elif capability.executor == "registered_config":
            adapter = RegisteredConfigAdapter()
        elif capability.name.startswith("host."):
            adapter = HostAdapter()
        elif environment.runtime_type == "docker_compose":
            adapter = DockerComposeAdapter()
        elif environment.runtime_type == "kubernetes":
            adapter = KubernetesAdapter()
        elif environment.runtime_type == "systemd":
            adapter = SystemdAdapter()
        else:
            result = AdapterResult("failed", "Runtime adapter is not available", {}, error=environment.runtime_type)
            return self._record(db, action, "runtime", result)
        result = adapter.execute(capability.name, args, connection, environment)
        return self._record(db, action, adapter.__class__.__name__, result)

    def _record(self, db, action, executor_type: str, result: AdapterResult) -> dict:
        evidence = record_result(db, action, executor_type, result)
        db.flush()
        return {
            "evidence_id": evidence.id,
            "capability": action.capability_name,
            "status": evidence.status,
            "summary": evidence.summary,
            "data": evidence.data_json,
            "observed_at": evidence.observed_at.isoformat(),
            "fresh_until": evidence.fresh_until.isoformat() if evidence.fresh_until else None,
        }
