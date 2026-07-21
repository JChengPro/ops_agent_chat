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
from app.policy.action_hash import configuration_revision
from app.models.project import Connection, Environment
from app.runtime.executor import RuntimeExecutor
from app.runtime.adapters.base import CommandAdapter
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
        id=11,
        project_id=3,
        runtime_type="docker_compose",
        workdir="/srv/app",
        namespace=None,
        connection_id=7,
        policy_profile="development",
        config_json={"compose_file": "compose.yml", "registered_deployments": {"api": {"service": "api-v1", "rollback": "restart"}}},
    )
    connection = SimpleNamespace(id=7, connection_type="ssh", host="host-a", port=22, username="ops", credential_ref="/run/secrets/key", host_fingerprint="SHA256:first", config_json={})
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


def test_rollback_fails_closed_after_runtime_configuration_drift():
    environment = SimpleNamespace(
        id=5,
        project_id=2,
        runtime_type="docker_compose",
        connection_id=8,
        workdir="/srv/app",
        namespace=None,
        config_json={"compose_file": "compose.yml"},
        policy_profile="development",
    )
    connection = SimpleNamespace(
        id=8,
        connection_type="ssh",
        host="host-a",
        port=22,
        username="ops",
        credential_ref="/run/secrets/key",
        host_fingerprint="SHA256:first",
        config_json={},
    )
    action = Action(
        environment_id=environment.id,
        config_revision=configuration_revision(environment, connection),
        resolved_spec_json={},
        rollback_spec_json={"kind": "capability", "capability": "service.start"},
    )
    connection.host = "host-b"

    class DB:
        def get(self, model, identifier):
            if model is Environment and identifier == environment.id:
                return environment
            if model is Connection and identifier == connection.id:
                return connection
            return None

    result = RuntimeExecutor().rollback(DB(), action, registry.get("service.restart"))
    assert result["status"] == "failed"
    assert "configuration changed" in result["summary"]


def test_no_op_rollback_never_claims_unverified_recovery():
    environment = SimpleNamespace(
        id=5, project_id=2, runtime_type="docker_compose", connection_id=None,
        workdir="/srv/app", namespace=None, config_json={"compose_file": "compose.yml"},
        policy_profile="development",
    )
    action = Action(
        environment_id=environment.id,
        config_revision=configuration_revision(environment, None),
        resolved_spec_json={},
        rollback_spec_json={"kind": "no_op", "reason": "service was already running"},
    )

    class DB:
        def get(self, model, identifier):
            return environment if model is Environment and identifier == environment.id else None

    result = RuntimeExecutor().rollback(DB(), action, registry.get("service.start"))
    assert result["status"] == "failed"
    assert "could not be independently verified" in result["summary"]


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


def test_missing_ssh_key_is_classified_and_stops_agent_retry(tmp_path):
    connection = SimpleNamespace(
        host="host.docker.internal",
        port=22,
        username="opsagent",
        credential_ref=str(tmp_path / "missing-key"),
        host_fingerprint="SHA256:test",
    )
    environment = SimpleNamespace(workdir="/srv/app")

    result = SSHTransport().execute(connection, environment, ["docker", "compose", "ps"])

    assert result.status == "failed"
    assert result.error_code == "ssh_credential_missing"
    assert "SSH 私钥" in result.stderr
    adapted = CommandAdapter().run(connection, environment, ["docker", "compose", "ps"], "Listed services")
    assert adapted.error_code == "ssh_credential_missing"
    assert adapted.summary == "运行容器中缺少 SSH 私钥"
    terminal = OpsAgentGraph._terminal_runtime_error({
        "status": "failed",
        "data": {"error_code": adapted.error_code},
    })
    assert terminal and terminal[0] == "ssh_credential_missing"
    assert "force-recreate backend worker" in terminal[1]


def test_ssh_transport_reuses_one_active_client_until_cycle_close(monkeypatch):
    class ActiveTransport:
        @staticmethod
        def is_active():
            return True

    class Client:
        def __init__(self):
            self.connect_count = 0
            self.close_count = 0

        def connect(self, **kwargs):
            del kwargs
            self.connect_count += 1

        @staticmethod
        def get_transport():
            return ActiveTransport()

        def close(self):
            self.close_count += 1

    client = Client()
    transport = SSHTransport(reuse_connections=True)
    monkeypatch.setattr(transport, "_client", lambda connection: client)
    monkeypatch.setattr(transport, "_verify_fingerprint", lambda current, connection: None)
    connection = SimpleNamespace(
        host="host.docker.internal",
        port=22,
        username="opsagent",
        credential_ref="/run/secrets/project-key",
        host_fingerprint="SHA256:test",
    )

    first, first_owned = transport._acquire_client(connection)
    second, second_owned = transport._acquire_client(connection)

    assert first is second is client
    assert first_owned is False
    assert second_owned is False
    assert client.connect_count == 1
    assert client.close_count == 0
    transport.close()
    assert client.close_count == 1


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


@pytest.mark.parametrize("capability", ["service.stop", "service.restart"])
def test_kubernetes_rollback_preserves_observed_replica_count(capability):
    action = Action(
        capability_name=capability,
        arguments_json={"service": "backend"},
        resolved_spec_json={"runtime_type": "kubernetes"},
    )
    observed = {
        "status": "success",
        "data": {"stdout": json.dumps({"spec": {"replicas": 3}, "status": {"availableReplicas": 3}})},
    }
    rollback = OpsAgentGraph._build_rollback_spec(action, registry.get(capability), observed)
    assert rollback == {
        "kind": "capability",
        "capability": "service.scale",
        "arguments": {"service": "backend", "replicas": 3},
    }


def test_docker_scale_rollback_preserves_total_observed_instances():
    action = Action(
        capability_name="service.scale",
        arguments_json={"service": "backend", "replicas": 4},
        resolved_spec_json={"runtime_type": "docker_compose"},
    )
    observed = {
        "status": "success",
        "data": {"stdout": '{"State":"running"}\n{"State":"exited"}'},
    }
    rollback = OpsAgentGraph._build_rollback_spec(action, registry.get("service.scale"), observed)
    assert rollback["arguments"]["replicas"] == 2


@pytest.mark.parametrize("capability", ["service.start", "service.stop", "service.restart"])
def test_unknown_original_state_never_invents_rollback(capability):
    action = Action(capability_name=capability, arguments_json={"service": "backend"}, resolved_spec_json={})
    definition = registry.get(capability)
    rollback = OpsAgentGraph._build_rollback_spec(action, definition, {"status": "success", "data": {"stdout": ""}})
    assert rollback["kind"] == "unavailable"
