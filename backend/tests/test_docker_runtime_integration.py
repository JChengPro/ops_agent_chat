import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime.adapters.docker import DockerComposeAdapter
from app.runtime.transports.ssh import TransportResult
from app.runtime.verification import verification_satisfied


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_INTEGRATION") != "1" or not shutil.which("docker"),
    reason="set RUN_DOCKER_INTEGRATION=1 on a host with Docker to run the real adapter test",
)


class LocalTransport:
    def execute(self, connection, environment, argv, *, timeout_seconds=20, cancel_check=None):
        del connection
        if cancel_check and cancel_check():
            return TransportResult("cancelled", None, "", "cancelled", 0)
        completed = subprocess.run(
            argv,
            cwd=environment.workdir,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return TransportResult(
            "success" if completed.returncode == 0 else "failed",
            completed.returncode,
            completed.stdout,
            completed.stderr,
            0,
        )


def test_real_docker_compose_adapter_change_and_verification_matrix(tmp_path):
    project_name = f"ops-agent-adapter-{uuid4().hex[:8]}"
    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"""name: {project_name}
services:
  probe:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: integration-test-only
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 1s
      timeout: 1s
      retries: 10
  unhealthy:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: integration-test-only
    healthcheck:
      test: ["CMD-SHELL", "exit 1"]
      interval: 1s
      timeout: 1s
      retries: 1
""",
        encoding="utf-8",
    )
    environment = SimpleNamespace(workdir=str(tmp_path), config_json={"compose_file": "compose.yml"})
    command = ["docker", "compose", "-f", str(compose)]
    try:
        subprocess.run(command + ["up", "-d"], check=True, timeout=60, capture_output=True, text=True)
        adapter = DockerComposeAdapter(LocalTransport())
        deadline = time.monotonic() + 30
        result = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        while time.monotonic() < deadline and result.data.get("healthy_count") != 1:
            time.sleep(0.5)
            result = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        assert result.status == "success"
        records = json.loads(result.data["stdout"])
        records = records if isinstance(records, list) else [records]
        assert records and records[0]["State"].lower() == "running"
        restart_action = SimpleNamespace(
            capability_name="service.restart",
            arguments_json={"service": "probe"},
            resolved_spec_json={"runtime_type": "docker_compose"},
        )
        assert verification_satisfied(restart_action, {"status": result.status, "data": result.data})

        changed = adapter.execute("service.stop", {"service": "probe"}, object(), environment)
        assert changed.status == "success"
        stopped = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        stop_action = SimpleNamespace(
            capability_name="service.stop",
            arguments_json={"service": "probe"},
            resolved_spec_json={"runtime_type": "docker_compose"},
        )
        assert verification_satisfied(stop_action, {"status": stopped.status, "data": stopped.data})

        assert adapter.execute("service.start", {"service": "probe"}, object(), environment).status == "success"
        start_action = SimpleNamespace(
            capability_name="service.start",
            arguments_json={"service": "probe"},
            resolved_spec_json={"runtime_type": "docker_compose"},
        )
        deadline = time.monotonic() + 30
        started = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        while time.monotonic() < deadline and not verification_satisfied(start_action, {"status": started.status, "data": started.data}):
            time.sleep(0.5)
            started = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        assert verification_satisfied(start_action, {"status": started.status, "data": started.data})

        assert adapter.execute("service.restart", {"service": "probe"}, object(), environment).status == "success"
        deadline = time.monotonic() + 30
        restarted = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        while time.monotonic() < deadline and not verification_satisfied(restart_action, {"status": restarted.status, "data": restarted.data}):
            time.sleep(0.5)
            restarted = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        assert verification_satisfied(restart_action, {"status": restarted.status, "data": restarted.data})

        assert adapter.execute("service.scale", {"service": "probe", "replicas": 2}, object(), environment).status == "success"
        scaled = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        scale_action = SimpleNamespace(
            capability_name="service.scale",
            arguments_json={"service": "probe", "replicas": 2},
            resolved_spec_json={"runtime_type": "docker_compose"},
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not verification_satisfied(scale_action, {"status": scaled.status, "data": scaled.data}):
            time.sleep(0.5)
            scaled = adapter.execute("service.status", {"service": "probe"}, object(), environment)
        assert verification_satisfied(scale_action, {"status": scaled.status, "data": scaled.data})

        deadline = time.monotonic() + 30
        unhealthy = adapter.execute("service.status", {"service": "unhealthy"}, object(), environment)
        while time.monotonic() < deadline and not any(
            str(item.get("Health") or "").lower() == "unhealthy" for item in unhealthy.data.get("records", [])
        ):
            time.sleep(0.5)
            unhealthy = adapter.execute("service.status", {"service": "unhealthy"}, object(), environment)
        unhealthy_action = SimpleNamespace(
            capability_name="service.restart",
            arguments_json={"service": "unhealthy"},
            resolved_spec_json={"runtime_type": "docker_compose"},
        )
        assert not verification_satisfied(unhealthy_action, {"status": unhealthy.status, "data": unhealthy.data})
    finally:
        subprocess.run(command + ["down", "-v", "--remove-orphans"], check=False, timeout=60, capture_output=True, text=True)
