from dataclasses import dataclass
from typing import Any

from app.models.project import Connection, Environment
from app.runtime.transports.ssh import SSHTransport, TransportResult


@dataclass(frozen=True)
class AdapterResult:
    status: str
    summary: str
    data: dict[str, Any]
    raw_output: str = ""
    error: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    truncated: bool = False


class CommandAdapter:
    executor_type = "ssh"

    def __init__(self, transport: SSHTransport | None = None) -> None:
        self.transport = transport or SSHTransport()

    def run(self, connection: Connection, environment: Environment, argv: list[str], summary: str) -> AdapterResult:
        result: TransportResult = self.transport.execute(connection, environment, argv)
        return AdapterResult(
            status=result.status,
            summary=summary if result.status == "success" else f"{summary} failed",
            data={"stdout": result.stdout, "stderr": result.stderr},
            raw_output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            truncated=result.truncated,
        )
