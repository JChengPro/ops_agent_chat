import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime.adapters.docker import DockerComposeAdapter
from app.runtime.transports.ssh import TransportResult


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


def test_real_docker_compose_adapter_reads_running_service(tmp_path):
    project_name = f"ops-agent-adapter-{uuid4().hex[:8]}"
    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"""name: {project_name}
services:
  probe:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: integration-test-only
""",
        encoding="utf-8",
    )
    environment = SimpleNamespace(workdir=str(tmp_path), config_json={"compose_file": "compose.yml"})
    command = ["docker", "compose", "-f", str(compose)]
    try:
        subprocess.run(command + ["up", "-d"], check=True, timeout=60, capture_output=True, text=True)
        result = DockerComposeAdapter(LocalTransport()).execute("service.status", {"service": "probe"}, object(), environment)
        assert result.status == "success"
        records = json.loads(result.data["stdout"])
        records = records if isinstance(records, list) else [records]
        assert records and records[0]["State"].lower() == "running"
    finally:
        subprocess.run(command + ["down", "-v", "--remove-orphans"], check=False, timeout=60, capture_output=True, text=True)
