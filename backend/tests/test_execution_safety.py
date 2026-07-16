import hashlib
import json
import stat
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.graph import OpsAgentGraph, approval_summaries, resolve_action_spec
from app.api.projects import EnvironmentPayload, validate_relative_path
from app.capabilities.registry import CapabilityRegistry, registry
from app.models.action import Action
from app.policy.action_hash import compute_action_hash
from app.runtime.adapters.http import HttpAdapter
from app.runtime.adapters.registered import RegisteredConfigAdapter, RegisteredDeploymentAdapter
from app.runtime.transports.ssh import SSHTransport, TransportResult


class FakeCommandTransport:
    def __init__(self, stdout: str, *, status: str = "success") -> None:
        self.stdout = stdout
        self.status = status

    def execute(self, connection, environment, argv, *, cancel_check=None, timeout_seconds=20):
        del connection, environment, argv, cancel_check, timeout_seconds
        return TransportResult(self.status, 0 if self.status == "success" else 1, self.stdout, "", 1)


def runtime_environment(runtime_type="docker_compose"):
    return SimpleNamespace(runtime_type=runtime_type, workdir="/srv/app", namespace="default", config_json={"compose_file": "compose.yml"})


def test_resolved_spec_is_immutable_and_changes_the_approval_hash():
    definition = registry.get("deployment.apply_registered")
    environment = SimpleNamespace(
        runtime_type="docker_compose",
        workdir="/srv/app",
        namespace=None,
        connection_id=7,
        config_json={"compose_file": "compose.yml", "registered_deployments": {"api": {"service": "api-v1", "rollback": "restart"}}},
    )
    connection = SimpleNamespace(id=7, connection_type="ssh", host="host-a", port=22, username="ops", credential_ref="/run/secrets/key", host_fingerprint="SHA256:first")
    resolved = resolve_action_spec(environment, definition, {"deployment": "api"}, "operation-1", connection)
    snapshot = {"capability": definition.name, "arguments": {"deployment": "api"}, "resolved_spec": resolved}
    approved_hash = compute_action_hash(snapshot)
    environment.config_json["registered_deployments"]["api"]["service"] = "api-v2"
    connection.host = "host-b"
    assert resolved["registered_deployment"]["service"] == "api-v1"
    assert resolved["connection"]["host"] == "host-a"
    assert compute_action_hash(snapshot) == approved_hash
    changed = {**snapshot, "resolved_spec": {**resolved, "registered_deployment": {"service": "api-v2", "rollback": "restart"}}}
    assert compute_action_hash(changed) != approved_hash


def test_unknown_change_verification_fails_closed():
    action = Action(capability_name="future.change", arguments_json={})
    assert not OpsAgentGraph._verification_satisfied(action, {"status": "success", "data": {"ok": True}})


def test_registered_docker_deployment_rejects_exited_or_wrong_instance_count():
    resolved = {"registered_deployment": {"service": "api", "rollback": "restart", "expected_instances": 1}}
    exited = RegisteredDeploymentAdapter(FakeCommandTransport(json.dumps({"State": "exited", "ExitCode": 1}))).execute(
        "deployment.verify_registered", {"deployment": "api"}, object(), runtime_environment(), resolved
    )
    assert exited.status == "failed"
    resolved["registered_deployment"]["expected_instances"] = 2
    one_running = RegisteredDeploymentAdapter(FakeCommandTransport(json.dumps({"State": "running", "ExitCode": 0, "Health": "healthy"}))).execute(
        "deployment.verify_registered", {"deployment": "api"}, object(), runtime_environment(), resolved
    )
    assert one_running.status == "failed"


def test_registered_config_requires_a_real_precondition():
    content = "enabled: true\n"
    resolved = {"registered_config_change": {"path": "config/app.yml", "content": content}}

    class FileTransport:
        def read_file(self, connection, environment, path):
            del connection, environment, path
            return "enabled: false\n"

    result = RegisteredConfigAdapter(FileTransport()).execute(
        "config.precheck_registered", {"change": "safe"}, object(), runtime_environment(), resolved
    )
    assert result.status == "failed"
    assert result.error == "missing_current_sha256"
    with pytest.raises(ValidationError):
        EnvironmentPayload(runtime_type="docker_compose", config_json={
            "registered_config_changes": {"safe": {"path": "config/app.yml", "content": content}},
        })


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "config/../../outside", "bad\npath"])
def test_registered_paths_cannot_escape_the_environment_workdir(path):
    with pytest.raises(ValueError):
        validate_relative_path(path)


def test_config_rollback_does_not_delete_an_original_already_restored_after_write_failure():
    original = "enabled: false\n"

    class FileTransport:
        def read_file(self, connection, environment, path):
            del connection, environment, path
            return original
        def restore_registered_file(self, *args):
            raise AssertionError("restore must not run when the original hash is already present")

    resolved = {
        "registered_config_change": {
            "path": "config/app.yml", "content": "enabled: true\n",
            "current_sha256": hashlib.sha256(original.encode()).hexdigest(),
        },
        "backup_path": "config/app.yml.ops-agent.backup.operation",
    }
    result = RegisteredConfigAdapter(FileTransport()).rollback(object(), runtime_environment(), resolved)
    assert result.status == "success"
    assert "original" in result.summary.lower()


@pytest.mark.parametrize(
    ("relation", "value"),
    [("executor", "not_real"), ("precheck", "missing.precheck"), ("verifier", "missing.verifier"), ("rollback", "missing.rollback")],
)
def test_registry_rejects_invalid_executors_and_references(tmp_path, relation, value):
    read = {
        "name": "test.read", "description": "read", "effect": "read", "risk_level": "L0",
        "runtimes": ["manual"], "permission": "runtime.read", "approval_mode": "never", "executor": "context",
    }
    if relation == "executor":
        read["executor"] = value
        definitions = [read]
    else:
        change = {
            "name": "test.change", "description": "change", "effect": "change", "risk_level": "L2",
            "runtimes": ["manual"], "permission": "runtime.change", "approval_mode": "always", "executor": "runtime",
            "precheck": "test.read", "verifier": "test.read", "rollback": None,
        }
        change[relation] = value
        definitions = [read, change]
    (tmp_path / "bad.yml").write_text(json.dumps(definitions), encoding="utf-8")
    with pytest.raises(ValueError):
        CapabilityRegistry(tmp_path)


def test_http_redirect_is_not_healthy_and_request_uses_pinned_address(monkeypatch):
    class Response:
        status_code = 302

        def __enter__(self): return self
        def __exit__(self, *args): return None
        def iter_bytes(self): yield b"redirect"

    captured = {}

    class Client:
        def __init__(self, **kwargs): captured["client"] = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def stream(self, method, url, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return Response()

    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("10.0.0.8", 8080))])
    monkeypatch.setattr("app.runtime.adapters.http.httpx.Client", Client)
    environment = SimpleNamespace(config_json={"health_endpoints": {"default": "http://service.internal:8080/health"}})
    result = HttpAdapter().execute({"endpoint": "default"}, environment)
    assert result.status == "failed"
    assert result.data["status_code"] == 302
    assert captured["url"] == "http://10.0.0.8:8080/health"
    assert captured["headers"]["Host"] == "service.internal:8080"


def test_sftp_safe_target_rejects_symlink_components():
    class Sftp:
        def normalize(self, path): return path
        def lstat(self, path):
            mode = stat.S_IFLNK if path == "/srv/app/config" else stat.S_IFREG
            return SimpleNamespace(st_mode=mode)

    with pytest.raises(ValueError, match="Symbolic links"):
        SSHTransport._safe_remote_target(Sftp(), "/srv/app", "config/app.yml", allow_missing_leaf=False)


def test_registered_file_write_failure_restores_original_backup(monkeypatch):
    renames: list[tuple[str, str]] = []

    class Sftp:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def normalize(self, path): return path
        def lstat(self, path):
            if ".backup." in path or ".ops-agent." in path:
                raise FileNotFoundError(2, "missing", path)
            return SimpleNamespace(st_mode=stat.S_IFDIR if path.endswith("/config") else stat.S_IFREG)
        def posix_rename(self, source, target): renames.append((source, target))
        def open(self, path, mode):
            del path, mode
            raise OSError("synthetic write failure")
        def remove(self, path): raise FileNotFoundError(2, "missing", path)

    class Client:
        def connect(self, **kwargs): del kwargs
        def close(self): pass
        def open_sftp(self): return Sftp()

    transport = SSHTransport()
    monkeypatch.setattr(transport, "_client", lambda connection: Client())
    monkeypatch.setattr(transport, "_verify_fingerprint", lambda client, connection: None)
    connection = SimpleNamespace(host="host", port=22, username="ops", credential_ref=None)
    environment = SimpleNamespace(workdir="/srv/app")
    result = transport.write_registered_file(
        connection, environment, "config/app.yml", "new", backup_path="config/app.yml.ops-agent.backup.operation"
    )
    assert result.status == "failed"
    assert renames == [
        ("/srv/app/config/app.yml", "/srv/app/config/app.yml.ops-agent.backup.operation"),
        ("/srv/app/config/app.yml.ops-agent.backup.operation", "/srv/app/config/app.yml"),
    ]


def test_approval_summary_describes_real_rollback_in_natural_language():
    _, risk = approval_summaries("service.restart", {"name": "backend"}, {"kind": "capability", "capability": "service.start"})
    assert "service.restart" not in risk
    assert "重新启动服务" in risk
    assert "自动回滚步骤" not in risk


def test_restart_rollback_restores_the_observed_original_state():
    action = Action(capability_name="service.restart", arguments_json={"service": "backend"}, resolved_spec_json={})
    running = {"status": "success", "data": {"stdout": '{"State":"running"}'}}
    stopped = {"status": "success", "data": {"stdout": '{"State":"exited"}'}}
    definition = registry.get("service.restart")
    assert OpsAgentGraph._build_rollback_spec(action, definition, running)["capability"] == "service.start"
    assert OpsAgentGraph._build_rollback_spec(action, definition, stopped)["capability"] == "service.stop"
