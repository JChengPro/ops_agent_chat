import hashlib
from pathlib import PurePosixPath

from app.runtime.adapters.base import AdapterResult, CommandAdapter


class RegisteredDeploymentAdapter(CommandAdapter):
    def execute(self, capability, args, connection, environment):
        key = args.get("deployment"); spec = (environment.config_json.get("registered_deployments") or {}).get(key)
        if not isinstance(spec, dict): return AdapterResult("failed", "Deployment recipe is not registered", {}, error=str(key))
        service = str(spec.get("service") or "")
        if not service: return AdapterResult("failed", "Registered deployment has no service", {}, error=str(key))
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
        if capability == "deployment.verify_registered" and environment.runtime_type == "docker_compose" and result.status == "success" and not str(result.data.get("stdout") or "").strip():
            return AdapterResult("failed", "Registered deployment service was not found", result.data, raw_output=result.raw_output, error="service_not_found", exit_code=result.exit_code, duration_ms=result.duration_ms, truncated=result.truncated)
        return result


class RegisteredConfigAdapter(CommandAdapter):
    def execute(self, capability, args, connection, environment):
        key = args.get("change"); spec = (environment.config_json.get("registered_config_changes") or {}).get(key)
        if not isinstance(spec, dict): return AdapterResult("failed", "Configuration change is not registered", {}, error=str(key))
        path = str(spec.get("path") or ""); content = spec.get("content")
        if not path or not isinstance(content, str): return AdapterResult("failed", "Registered configuration change is invalid", {}, error=str(key))
        expected = hashlib.sha256(content.encode()).hexdigest()
        if capability in {"config.verify_registered", "config.precheck_registered"}:
            try: actual_content = self.transport.read_file(connection, environment, path)
            except Exception as exc: return AdapterResult("failed", "Registered configuration verification failed", {"path": path}, error=str(exc))
            actual = hashlib.sha256(actual_content.encode()).hexdigest()
            wanted = str(spec.get("current_sha256") or actual) if capability == "config.precheck_registered" else expected
            status = "success" if actual == wanted else "failed"
            return AdapterResult(status, "Registered configuration hash verified" if status == "success" else "Registered configuration hash mismatch", {"path": path, "expected_sha256": wanted, "actual_sha256": actual})
        current_expected = spec.get("current_sha256")
        if current_expected:
            try: current = self.transport.read_file(connection, environment, path)
            except Exception as exc: return AdapterResult("failed", "Unable to read current registered configuration", {"path": path}, error=str(exc))
            if hashlib.sha256(current.encode()).hexdigest() != current_expected: return AdapterResult("failed", "Current configuration does not match registered precondition", {"path": path})
        result = self.transport.write_registered_file(connection, environment, path, content)
        return AdapterResult(result.status, "Registered configuration updated" if result.status == "success" else "Registered configuration update failed", {"path": path, "expected_sha256": expected}, raw_output=result.stdout, error=result.stderr, exit_code=result.exit_code, duration_ms=result.duration_ms)
