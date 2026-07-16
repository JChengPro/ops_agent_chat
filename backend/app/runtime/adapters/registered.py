import hashlib
import json
from pathlib import PurePosixPath

from app.runtime.adapters.base import AdapterResult, CommandAdapter


class RegisteredDeploymentAdapter(CommandAdapter):
    def execute(self, capability, args, connection, environment, resolved_spec=None):
        key = args.get("deployment")
        spec = (resolved_spec or {}).get("registered_deployment")
        if spec is None:
            spec = (environment.config_json.get("registered_deployments") or {}).get(key)
        if not isinstance(spec, dict): return AdapterResult("failed", "Deployment recipe is not registered", {}, error=str(key))
        service = str(spec.get("service") or "")
        if not service: return AdapterResult("failed", "Registered deployment has no service", {}, error=str(key))
        if capability == "deployment.precheck_registered":
            return AdapterResult("success", "Registered deployment recipe is valid", {"deployment": key, "service": service})
        if environment.runtime_type == "docker_compose":
            compose = str(environment.config_json.get("compose_file") or "docker-compose.yml")
            argv = ["docker", "compose", "-f", compose]
            if capability == "deployment.apply_registered": argv += ["up", "-d", "--no-deps", service]
            else: argv += ["ps", "--all", "--format", "json", service]
        elif environment.runtime_type == "kubernetes":
            namespace = environment.namespace or "default"; argv = ["kubectl", "-n", namespace]
            if capability == "deployment.apply_registered":
                manifest = str(spec.get("manifest") or "")
                path = PurePosixPath(manifest)
                if not manifest or path.is_absolute() or ".." in path.parts: return AdapterResult("failed", "Registered manifest path is invalid", {}, error=manifest)
                argv += ["apply", "-f", manifest]
            else: argv += ["rollout", "status", f"deployment/{service}", "--timeout=30s"]
        else: return AdapterResult("failed", "Registered deployment runtime is unsupported", {}, error=environment.runtime_type)
        result = self.run(connection, environment, argv, f"{capability} for {key}")
        if capability == "deployment.verify_registered" and environment.runtime_type == "docker_compose" and result.status == "success":
            records = _compose_records(str(result.data.get("stdout") or ""))
            expected_instances = int(spec.get("expected_instances") or 1)
            healthy = len(records) == expected_instances and all(
                str(item.get("State") or "").lower() == "running"
                and str(item.get("Health") or "healthy").lower() in {"", "healthy"}
                and int(item.get("ExitCode") or 0) == 0
                for item in records
            )
            if not healthy:
                return AdapterResult("failed", "Registered deployment is not running and healthy", {**result.data, "records": records, "expected_instances": expected_instances}, raw_output=result.raw_output, error="deployment_unhealthy", exit_code=result.exit_code, duration_ms=result.duration_ms, truncated=result.truncated)
            return AdapterResult("success", "Registered deployment is running and healthy", {**result.data, "records": records, "expected_instances": expected_instances}, raw_output=result.raw_output, exit_code=result.exit_code, duration_ms=result.duration_ms, truncated=result.truncated)
        if capability == "deployment.verify_registered" and environment.runtime_type == "kubernetes" and result.status == "success":
            return AdapterResult("success", "Registered deployment rollout is ready", {**result.data, "deployment_ready": True}, raw_output=result.raw_output, exit_code=result.exit_code, duration_ms=result.duration_ms, truncated=result.truncated)
        return result


class RegisteredConfigAdapter(CommandAdapter):
    def execute(self, capability, args, connection, environment, resolved_spec=None):
        key = args.get("change")
        spec = (resolved_spec or {}).get("registered_config_change")
        if spec is None:
            spec = (environment.config_json.get("registered_config_changes") or {}).get(key)
        if not isinstance(spec, dict): return AdapterResult("failed", "Configuration change is not registered", {}, error=str(key))
        path = str(spec.get("path") or ""); content = spec.get("content")
        if not path or not isinstance(content, str): return AdapterResult("failed", "Registered configuration change is invalid", {}, error=str(key))
        expected = hashlib.sha256(content.encode()).hexdigest()
        if capability == "config.precheck_registered":
            current_expected = spec.get("current_sha256")
            if not current_expected and not spec.get("allow_create"):
                return AdapterResult("failed", "Registered configuration requires current_sha256", {"path": path}, error="missing_current_sha256")
            try: actual_content = self.transport.read_file(connection, environment, path)
            except Exception as exc:
                if spec.get("allow_create") and getattr(exc, "errno", None) == 2:
                    return AdapterResult("success", "Registered configuration target is absent and may be created", {"path": path, "allow_create": True})
                return AdapterResult("failed", "Registered configuration precheck failed", {"path": path}, error=str(exc))
            actual = hashlib.sha256(actual_content.encode()).hexdigest()
            if spec.get("allow_create"):
                return AdapterResult("failed", "Registered configuration target already exists", {"path": path, "actual_sha256": actual}, error="target_exists")
            status = "success" if actual == current_expected else "failed"
            return AdapterResult(status, "Registered configuration precondition verified" if status == "success" else "Registered configuration hash mismatch", {"path": path, "expected_sha256": current_expected, "actual_sha256": actual})
        if capability == "config.verify_registered":
            try: actual_content = self.transport.read_file(connection, environment, path)
            except Exception as exc: return AdapterResult("failed", "Registered configuration verification failed", {"path": path}, error=str(exc))
            actual = hashlib.sha256(actual_content.encode()).hexdigest()
            status = "success" if actual == expected else "failed"
            return AdapterResult(status, "Registered configuration hash verified" if status == "success" else "Registered configuration hash mismatch", {"path": path, "expected_sha256": expected, "actual_sha256": actual})
        current_expected = spec.get("current_sha256")
        if current_expected:
            try: current = self.transport.read_file(connection, environment, path)
            except Exception as exc: return AdapterResult("failed", "Unable to read current registered configuration", {"path": path}, error=str(exc))
            if hashlib.sha256(current.encode()).hexdigest() != current_expected: return AdapterResult("failed", "Current configuration does not match registered precondition", {"path": path})
        backup_path = (resolved_spec or {}).get("backup_path")
        result = self.transport.write_registered_file(connection, environment, path, content, backup_path=backup_path)
        return AdapterResult(result.status, "Registered configuration updated" if result.status == "success" else "Registered configuration update failed", {"path": path, "expected_sha256": expected, "backup_path": backup_path}, raw_output=result.stdout, error=result.stderr, exit_code=result.exit_code, duration_ms=result.duration_ms)

    def rollback(self, connection, environment, resolved_spec):
        spec = resolved_spec.get("registered_config_change") or {}
        path = str(spec.get("path") or "")
        backup_path = str(resolved_spec.get("backup_path") or "")
        if not path or not backup_path:
            return AdapterResult("failed", "Registered configuration rollback is unavailable", {}, error="missing_backup_spec")
        original_hash = spec.get("current_sha256")
        if original_hash:
            try:
                current = self.transport.read_file(connection, environment, path)
            except Exception:
                current = None
            if current is not None and hashlib.sha256(current.encode()).hexdigest() == original_hash:
                return AdapterResult("success", "Registered configuration already matches its original version", {"path": path, "original_sha256": original_hash})
        result = self.transport.restore_registered_file(connection, environment, path, backup_path)
        if result.status != "success":
            return AdapterResult("failed", "Registered configuration rollback failed", {"path": path, "backup_path": backup_path}, raw_output=result.stdout, error=result.stderr, exit_code=result.exit_code, duration_ms=result.duration_ms)
        try:
            restored = self.transport.read_file(connection, environment, path)
        except Exception as exc:
            if spec.get("allow_create") and getattr(exc, "errno", None) == 2:
                return AdapterResult("success", "Newly created configuration was removed", {"path": path, "backup_path": backup_path})
            return AdapterResult("failed", "Restored configuration could not be verified", {"path": path, "backup_path": backup_path}, error=str(exc))
        restored_hash = hashlib.sha256(restored.encode()).hexdigest()
        if not original_hash or restored_hash != original_hash:
            return AdapterResult("failed", "Restored configuration hash does not match the original", {"path": path, "backup_path": backup_path, "actual_sha256": restored_hash, "expected_sha256": original_hash})
        return AdapterResult("success", "Registered configuration restored and verified", {"path": path, "backup_path": backup_path, "actual_sha256": restored_hash})

    def finalize(self, connection, environment, resolved_spec):
        backup_path = str(resolved_spec.get("backup_path") or "")
        if backup_path:
            self.transport.remove_registered_backup(connection, environment, backup_path)


def rollback_deployment(adapter: RegisteredDeploymentAdapter, connection, environment, resolved_spec):
    spec = resolved_spec.get("registered_deployment") or {}
    service = str(spec.get("service") or "")
    mode = str(spec.get("rollback") or "rollout_undo")
    if not service:
        return AdapterResult("failed", "Deployment rollback target is missing", {}, error="missing_service")
    if environment.runtime_type == "docker_compose":
        compose = str(resolved_spec.get("compose_file") or "docker-compose.yml")
        argv = ["docker", "compose", "-f", compose, "stop" if mode == "stop" else "restart", service]
    elif environment.runtime_type == "kubernetes":
        argv = ["kubectl", "-n", environment.namespace or "default", "rollout", "undo", f"deployment/{service}"]
    else:
        return AdapterResult("failed", "Deployment rollback runtime is unsupported", {}, error=environment.runtime_type)
    changed = adapter.run(connection, environment, argv, f"Rolled back deployment {service}")
    if changed.status != "success":
        return changed
    if environment.runtime_type == "docker_compose":
        checked = adapter.run(connection, environment, ["docker", "compose", "-f", compose, "ps", "--all", "--format", "json", service], f"Verified rollback for deployment {service}")
        if checked.status != "success":
            return checked
        records = _compose_records(str(checked.data.get("stdout") or ""))
        if mode == "stop":
            valid = bool(records) and all(str(item.get("State") or "").lower() in {"exited", "stopped", "dead"} for item in records)
        else:
            expected_instances = int(spec.get("expected_instances") or 1)
            valid = len(records) == expected_instances and all(
                str(item.get("State") or "").lower() == "running"
                and str(item.get("Health") or "healthy").lower() in {"", "healthy"}
                and int(item.get("ExitCode") or 0) == 0
                for item in records
            )
        return AdapterResult("success" if valid else "failed", "Deployment rollback verified" if valid else "Deployment rollback verification failed", {**checked.data, "records": records}, raw_output=checked.raw_output, error=checked.error if valid else "rollback_verification_failed", exit_code=checked.exit_code, duration_ms=changed.duration_ms + checked.duration_ms)
    checked = adapter.run(connection, environment, ["kubectl", "-n", environment.namespace or "default", "rollout", "status", f"deployment/{service}", "--timeout=30s"], f"Verified rollback for deployment {service}")
    return checked


def _compose_records(raw: str) -> list[dict]:
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        records = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return records
