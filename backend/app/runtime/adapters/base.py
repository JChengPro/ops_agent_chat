from dataclasses import dataclass
from typing import Any, Callable

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
    error_code: str | None = None


class CommandAdapter:
    executor_type = "ssh"

    def __init__(self, transport: SSHTransport | None = None, cancel_check: Callable[[], bool] | None = None) -> None:
        self.transport = transport or SSHTransport()
        self.cancel_check = cancel_check

    def run(self, connection: Connection, environment: Environment, argv: list[str], summary: str) -> AdapterResult:
        result: TransportResult = self.transport.execute(connection, environment, argv, cancel_check=self.cancel_check)
        failure_summaries = {
            "ssh_credential_not_configured": "项目尚未配置 SSH 私钥",
            "ssh_credential_missing": "运行容器中缺少 SSH 私钥",
            "ssh_credential_unreadable": "运行容器无法读取 SSH 私钥",
            "ssh_authentication_failed": "SSH 身份验证失败",
            "ssh_host_key_mismatch": "SSH 主机指纹校验失败",
            "ssh_connection_timeout": "SSH 连接超时",
            "ssh_connection_failed": "SSH 连接失败",
        }
        failure_summary = failure_summaries.get(result.error_code) if result.error_code else None
        return AdapterResult(
            status=result.status,
            summary=(
                summary
                if result.status == "success"
                else failure_summary or f"{summary}执行失败"
            ),
            data={"stdout": result.stdout, "stderr": result.stderr},
            raw_output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            truncated=result.truncated,
            error_code=result.error_code,
        )
