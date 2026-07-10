from dataclasses import dataclass
from datetime import datetime, timezone
import time

import paramiko

from app.models.project import Project
from app.models.server import Server
from app.utils.redaction import truncate_text


@dataclass
class SSHExecutionResult:
    command: str
    cwd: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    stdout_truncated: bool
    stderr_truncated: bool
    status: str
    started_at: datetime
    finished_at: datetime


class SSHExecutor:
    def test_connection(self, server: Server) -> tuple[bool, str]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10,
                "banner_timeout": 10,
                "auth_timeout": 10,
            }
            if server.private_key_ref:
                connect_kwargs["key_filename"] = server.private_key_ref
            client.connect(**connect_kwargs)
            return True, "SSH connection succeeded"
        except Exception as exc:  # noqa: BLE001 - convert transport failure to API message.
            return False, f"SSH connection failed: {exc}"
        finally:
            client.close()

    def execute(self, server: Server, project: Project, command: str, timeout_seconds: int = 20) -> SSHExecutionResult:
        started = datetime.now(timezone.utc)
        begin = time.monotonic()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10,
                "banner_timeout": 10,
                "auth_timeout": 10,
            }
            if server.private_key_ref:
                connect_kwargs["key_filename"] = server.private_key_ref
            client.connect(**connect_kwargs)
            safe_command = f"cd {sh_quote(project.workdir)} && {command}"
            stdin, stdout, stderr = client.exec_command(safe_command, timeout=timeout_seconds)
            del stdin
            exit_code = stdout.channel.recv_exit_status()
            out_raw = stdout.read().decode("utf-8", errors="replace")
            err_raw = stderr.read().decode("utf-8", errors="replace")
            status = "success" if exit_code == 0 else "failed"
        except Exception as exc:  # noqa: BLE001 - executor must convert all transport errors into command result.
            exit_code = 255
            out_raw = ""
            err_raw = f"SSH execution failed: {exc}"
            status = "failed"
        finally:
            client.close()

        stdout_excerpt, stdout_truncated = truncate_text(out_raw, 12000)
        stderr_excerpt, stderr_truncated = truncate_text(err_raw, 6000)
        finished = datetime.now(timezone.utc)
        return SSHExecutionResult(
            command=command,
            cwd=project.workdir,
            stdout=stdout_excerpt,
            stderr=stderr_excerpt,
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - begin) * 1000),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            status=status,
            started_at=started,
            finished_at=finished,
        )


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
