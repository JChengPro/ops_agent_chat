import hashlib
import json
from typing import Any


def compute_action_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action_snapshot(action: Any) -> dict[str, Any]:
    return {
        "capability": action.capability_name,
        "version": action.capability_version,
        "definition_hash": action.capability_definition_hash,
        "risk_level": action.risk_level,
        "approval_mode": action.approval_mode,
        "policy_version": action.policy_version,
        "config_revision": action.config_revision,
        "project_id": action.project_id,
        "environment_id": action.environment_id,
        "target": action.target_json,
        "arguments": action.arguments_json,
        "resolved_spec": action.resolved_spec_json,
        "rollback_spec": action.rollback_spec_json,
        "effect": action.effect,
    }


def configuration_revision(environment: Any, connection: Any | None) -> str:
    payload = {
        "environment": {
            "id": environment.id,
            "project_id": environment.project_id,
            "runtime_type": environment.runtime_type,
            "connection_id": environment.connection_id,
            "workdir": environment.workdir,
            "namespace": environment.namespace,
            "config": environment.config_json or {},
            "policy_profile": environment.policy_profile,
        },
        "connection": None if connection is None else {
            "id": connection.id,
            "connection_type": connection.connection_type,
            "host": connection.host,
            "port": connection.port,
            "username": connection.username,
            "credential_ref": connection.credential_ref,
            "host_fingerprint": connection.host_fingerprint,
            "config": connection.config_json or {},
        },
    }
    return compute_action_hash(payload)
