from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.capabilities.schemas import CapabilityDefinition
from app.context.service import query_project_context, query_relationships
from app.evidence.service import record_result
from app.experience.service import search_experience
from app.models.action import Action, Approval, PolicyDecision
from app.models.agent import AgentRun
from app.capabilities.registry import registry
from app.policy.action_hash import compute_action_hash
from app.models.project import Connection, Environment
from app.policy.engine import PolicyEngine
from app.runtime.adapters.base import AdapterResult
from app.runtime.adapters.docker import DockerComposeAdapter
from app.runtime.adapters.host import HostAdapter
from app.runtime.adapters.http import HttpAdapter
from app.runtime.adapters.kubernetes import KubernetesAdapter
from app.runtime.adapters.registered import RegisteredConfigAdapter, RegisteredDeploymentAdapter, rollback_deployment
from app.runtime.adapters.systemd import SystemdAdapter


class RuntimeExecutor:
    def execute(self, db, action: Action, capability: CapabilityDefinition, *, ignore_cancellation: bool = False) -> dict:
        environment = db.get(Environment, action.environment_id)
        if not environment:
            result = AdapterResult("failed", "Environment is missing", {}, error="environment_not_found")
            return self._record(db, action, capability.executor, result)
        args = action.arguments_json
        resolved = action.resolved_spec_json or {}
        runtime_environment = SimpleNamespace(
            id=environment.id,
            project_id=environment.project_id,
            runtime_type=resolved.get("runtime_type", environment.runtime_type),
            workdir=resolved.get("workdir", environment.workdir),
            namespace=resolved.get("namespace", environment.namespace),
            connection_id=resolved.get("connection_id", environment.connection_id),
            config_json={"compose_file": resolved.get("compose_file", (environment.config_json or {}).get("compose_file", "docker-compose.yml"))},
        )
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
        connection = self._resolved_connection(db, environment, resolved)
        cancel_check = (lambda: False) if ignore_cancellation else (lambda: self._cancelled(action.run_id))
        if capability.name == "http.health_check":
            result = HttpAdapter(cancel_check=cancel_check).execute(args, environment)
            return self._record(db, action, "http", result)
        if not connection:
            result = AdapterResult("failed", "Runtime connection is not configured", {}, error="connection_not_found")
            return self._record(db, action, "runtime", result)
        if capability.executor == "registered_deployment":
            adapter = RegisteredDeploymentAdapter(cancel_check=cancel_check)
        elif capability.executor == "registered_config":
            adapter = RegisteredConfigAdapter(cancel_check=cancel_check)
        elif capability.name.startswith("host."):
            adapter = HostAdapter(cancel_check=cancel_check)
        elif runtime_environment.runtime_type == "docker_compose":
            adapter = DockerComposeAdapter(cancel_check=cancel_check)
        elif runtime_environment.runtime_type == "kubernetes":
            adapter = KubernetesAdapter(cancel_check=cancel_check)
        elif runtime_environment.runtime_type == "systemd":
            adapter = SystemdAdapter(cancel_check=cancel_check)
        else:
            result = AdapterResult("failed", "Runtime adapter is not available", {}, error=runtime_environment.runtime_type)
            return self._record(db, action, "runtime", result)
        if capability.executor in {"registered_deployment", "registered_config"}:
            result = adapter.execute(capability.name, args, connection, runtime_environment, resolved)
        else:
            result = adapter.execute(capability.name, args, connection, runtime_environment)
        return self._record(db, action, adapter.__class__.__name__, result)

    @staticmethod
    def _cancelled(run_id: str) -> bool:
        from app.core.database import SessionLocal
        from app.models.agent import AgentRun

        with SessionLocal() as check_db:
            run = check_db.get(AgentRun, run_id)
            return bool(not run or run.status == "cancelled" or run.cancel_requested_at)

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

    def rollback(self, db, action: Action, capability: CapabilityDefinition) -> dict:
        environment = db.get(Environment, action.environment_id)
        resolved = action.resolved_spec_json or {}
        connection = self._resolved_connection(db, environment, resolved) if environment else None
        kind = (action.rollback_spec_json or {}).get("kind")
        if kind == "no_op":
            return {"status": "success", "summary": "Rollback was not needed because the target already matched its original state"}
        if not environment or not connection:
            return {"status": "failed", "summary": "Rollback connection is unavailable"}
        runtime_environment = SimpleNamespace(
            id=environment.id, project_id=environment.project_id,
            runtime_type=resolved.get("runtime_type", environment.runtime_type),
            workdir=resolved.get("workdir", environment.workdir),
            namespace=resolved.get("namespace", environment.namespace),
            connection_id=resolved.get("connection_id", environment.connection_id),
            config_json={"compose_file": resolved.get("compose_file", "docker-compose.yml")},
        )
        if kind == "config_backup":
            result = RegisteredConfigAdapter().rollback(connection, runtime_environment, resolved)
            return self._record(db, action, "RegisteredConfigRollback", result)
        if kind == "deployment":
            adapter = RegisteredDeploymentAdapter()
            result = rollback_deployment(adapter, connection, runtime_environment, resolved)
            return self._record(db, action, "RegisteredDeploymentRollback", result)
        if kind == "capability":
            name = action.rollback_spec_json.get("capability")
            definition = registry.get(name)
            arguments = action.rollback_spec_json.get("arguments") or {}
            if not definition:
                return {"status": "failed", "summary": "Rollback capability is unavailable"}
            snapshot = {"capability": name, "version": definition.version, "project_id": action.project_id, "environment_id": action.environment_id, "target": action.target_json, "arguments": arguments, "resolved_spec": resolved, "rollback_spec": {}, "effect": "change"}
            rollback_action = Action(
                id=str(uuid4()), run_id=action.run_id, capability_name=name, capability_version=definition.version,
                project_id=action.project_id, environment_id=action.environment_id, target_json=action.target_json,
                arguments_json=arguments, resolved_spec_json=resolved, rollback_spec_json={}, purpose=f"Automatic rollback for {action.id}",
                effect="change", action_hash=compute_action_hash(snapshot), status="executing",
            )
            db.add(rollback_action); db.flush()
            run = db.get(AgentRun, action.run_id)
            policy = PolicyEngine().evaluate(db, rollback_action, definition, run.user_id if run else -1)
            db.add(PolicyDecision(
                action_id=rollback_action.id,
                decision=policy.decision,
                risk_level=policy.risk_level,
                reason_code=policy.reason_code,
                reason=policy.reason,
                matched_policies_json=policy.matched_policies,
            ))
            original_approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
            approved_snapshot = bool(
                original_approval
                and original_approval.decision == "approved"
                and original_approval.action_hash == action.action_hash
                and original_approval.action_hash == compute_action_hash({
                    "capability": action.capability_name,
                    "version": action.capability_version,
                    "project_id": action.project_id,
                    "environment_id": action.environment_id,
                    "target": action.target_json,
                    "arguments": action.arguments_json,
                    "resolved_spec": action.resolved_spec_json,
                    "rollback_spec": action.rollback_spec_json,
                    "effect": action.effect,
                })
            )
            if policy.decision == "require_approval" and not approved_snapshot:
                rollback_action.status = "denied"
                return {"status": "failed", "summary": "Automatic recovery is not covered by the approved action snapshot"}
            if policy.decision not in {"allow", "require_approval"}:
                rollback_action.status = "denied"
                return {"status": "failed", "summary": f"Automatic recovery was blocked by policy: {policy.reason}"}
            changed = self.execute(db, rollback_action, definition, ignore_cancellation=True)
            rollback_action.execution_finished_at = datetime.now(timezone.utc)
            if changed.get("status") != "success":
                rollback_action.status = "failed"
                return {"status": "failed", "summary": "Automatic recovery action failed", "change": changed}
            verifier = registry.get(definition.verifier) if definition.verifier else None
            if not verifier:
                rollback_action.status = "verification_failed"
                return {"status": "failed", "summary": "Automatic recovery has no registered verifier", "change": changed}
            verify_arguments = {name: arguments[name] for name in verifier.arguments if name in arguments}
            verify_snapshot = {"capability": verifier.name, "version": verifier.version, "project_id": action.project_id, "environment_id": action.environment_id, "target": action.target_json, "arguments": verify_arguments, "resolved_spec": resolved, "rollback_spec": {}, "effect": "read"}
            verify_action = Action(
                id=str(uuid4()), run_id=action.run_id, capability_name=verifier.name, capability_version=verifier.version,
                project_id=action.project_id, environment_id=action.environment_id, target_json=action.target_json,
                arguments_json=verify_arguments, resolved_spec_json=resolved, rollback_spec_json={},
                purpose=f"Verify automatic rollback for {action.id}", effect="read",
                action_hash=compute_action_hash(verify_snapshot), status="executing",
                execution_started_at=datetime.now(timezone.utc),
            )
            db.add(verify_action); db.flush()
            verification = self.execute(db, verify_action, verifier, ignore_cancellation=True)
            verify_action.status = "succeeded" if verification.get("status") == "success" else "failed"
            verify_action.execution_finished_at = datetime.now(timezone.utc)
            from app.agent.graph import OpsAgentGraph
            verified = OpsAgentGraph._verification_satisfied(rollback_action, verification)
            rollback_action.status = "verified" if verified else "verification_failed"
            return {
                "status": "success" if verified else "failed",
                "summary": "Automatic recovery completed and was verified" if verified else "Automatic recovery verification failed",
                "change": changed,
                "verification": verification,
            }
        return {"status": "failed", "summary": "No automatic rollback specification is available"}

    def finalize(self, db, action: Action, capability: CapabilityDefinition) -> None:
        if capability.executor != "registered_config":
            return
        environment = db.get(Environment, action.environment_id)
        resolved = action.resolved_spec_json or {}
        connection = self._resolved_connection(db, environment, resolved) if environment else None
        if not environment or not connection:
            return
        runtime_environment = SimpleNamespace(
            runtime_type=resolved.get("runtime_type", environment.runtime_type), workdir=resolved.get("workdir", environment.workdir),
            namespace=resolved.get("namespace", environment.namespace), config_json={"compose_file": resolved.get("compose_file", "docker-compose.yml")},
        )
        RegisteredConfigAdapter().finalize(connection, runtime_environment, resolved)

    @staticmethod
    def _resolved_connection(db, environment: Environment, resolved: dict):
        snapshot = resolved.get("connection")
        if isinstance(snapshot, dict) and snapshot.get("id"):
            return SimpleNamespace(
                id=snapshot.get("id"),
                connection_type=snapshot.get("connection_type"),
                host=snapshot.get("host"),
                port=snapshot.get("port"),
                username=snapshot.get("username"),
                credential_ref=snapshot.get("credential_ref"),
                host_fingerprint=snapshot.get("host_fingerprint"),
            )
        connection_id = resolved.get("connection_id", environment.connection_id)
        return db.get(Connection, connection_id) if connection_id else None
