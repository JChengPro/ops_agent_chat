from dataclasses import dataclass
import base64
import hashlib
from pathlib import PurePosixPath
import shlex
import time

import paramiko

from app.core.config import get_settings
from app.models.project import Connection, Environment
from app.utils.redaction import truncate_text


@dataclass(frozen=True)
class TransportResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False


class SSHTransport:
    def test_connection(self, connection: Connection) -> tuple[bool, str]:
        client = self._client(connection)
        try:
            client.connect(**self._connect_kwargs(connection))
            self._verify_fingerprint(client, connection)
            return True, "SSH connection succeeded"
        except Exception as exc:  # noqa: BLE001
            return False, f"SSH connection failed: {exc}"
        finally:
            client.close()

    def execute(
        self,
        connection: Connection,
        environment: Environment,
        argv: list[str],
        *,
        timeout_seconds: int = 20,
    ) -> TransportResult:
        if not argv or any("\x00" in item or "\n" in item for item in argv):
            return TransportResult("failed", None, "", "Invalid command arguments", 0)
        client = self._client(connection)
        start = time.monotonic()
        try:
            client.connect(**self._connect_kwargs(connection))
            self._verify_fingerprint(client, connection)
            command = shlex.join(argv)
            if environment.workdir:
                command = f"cd {shlex.quote(environment.workdir)} && {command}"
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout_seconds)
            stdin.close()
            channel = stdout.channel
            deadline = time.monotonic() + timeout_seconds
            out_buffer = bytearray()
            err_buffer = bytearray()
            out_stream_truncated = err_stream_truncated = False
            while True:
                while channel.recv_ready():
                    chunk = channel.recv(32768)
                    remaining = 65537 - len(out_buffer)
                    if remaining > 0:
                        out_buffer.extend(chunk[:remaining])
                    out_stream_truncated = out_stream_truncated or len(chunk) > remaining or len(out_buffer) > 65536
                while channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(16384)
                    remaining = 16385 - len(err_buffer)
                    if remaining > 0:
                        err_buffer.extend(chunk[:remaining])
                    err_stream_truncated = err_stream_truncated or len(chunk) > remaining or len(err_buffer) > 16384
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() >= deadline:
                    channel.close()
                    out, _ = truncate_text(out_buffer.decode("utf-8", errors="replace"), 65536)
                    err, _ = truncate_text(err_buffer.decode("utf-8", errors="replace"), 16384)
                    return TransportResult("failed", None, out, (err + "\nCommand timed out").strip(), int((time.monotonic() - start) * 1000), True)
                time.sleep(0.01)
            exit_code = channel.recv_exit_status()
            out_raw = out_buffer.decode("utf-8", errors="replace")
            err_raw = err_buffer.decode("utf-8", errors="replace")
            out, out_text_truncated = truncate_text(out_raw, 65536)
            err, err_text_truncated = truncate_text(err_raw, 16384)
            return TransportResult(
                "success" if exit_code == 0 else "failed",
                exit_code,
                out,
                err,
                int((time.monotonic() - start) * 1000),
                out_stream_truncated or err_stream_truncated or out_text_truncated or err_text_truncated,
            )
        except Exception as exc:  # noqa: BLE001
            return TransportResult("failed", None, "", f"SSH execution failed: {exc}", int((time.monotonic() - start) * 1000))
        finally:
            client.close()

    def read_file(self, connection: Connection, environment: Environment, relative_path: str) -> str:
        if not environment.workdir:
            raise ValueError("Environment workdir is not configured")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("File path must stay inside the environment workdir")
        target = str(PurePosixPath(environment.workdir) / relative)
        client = self._client(connection)
        try:
            client.connect(**self._connect_kwargs(connection))
            self._verify_fingerprint(client, connection)
            with client.open_sftp() as sftp:
                with sftp.open(target, "r") as handle:
                    raw = handle.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Context source file exceeds 2 MB")
            return raw.decode("utf-8") if isinstance(raw, bytes) else raw
        finally:
            client.close()

    def write_registered_file(self, connection: Connection, environment: Environment, relative_path: str, content: str) -> TransportResult:
        if not environment.workdir or len(content.encode("utf-8")) > 2_000_000:
            return TransportResult("failed", None, "", "Invalid workdir or registered content exceeds 2 MB", 0)
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return TransportResult("failed", None, "", "File path must stay inside the environment workdir", 0)
        target = str(PurePosixPath(environment.workdir) / relative)
        temporary = target + ".ops-agent.tmp"
        client = self._client(connection); start = time.monotonic()
        try:
            client.connect(**self._connect_kwargs(connection)); self._verify_fingerprint(client, connection)
            with client.open_sftp() as sftp:
                with sftp.open(temporary, "w") as handle: handle.write(content)
                sftp.posix_rename(temporary, target)
            return TransportResult("success", 0, "registered file updated", "", int((time.monotonic() - start) * 1000))
        except Exception as exc:  # noqa: BLE001
            return TransportResult("failed", None, "", f"Registered file update failed: {exc}", int((time.monotonic() - start) * 1000))
        finally:
            client.close()


    def _client(self, connection: Connection) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        strict = get_settings().ssh_strict_host_key_checking
        client.set_missing_host_key_policy(paramiko.RejectPolicy() if strict else paramiko.AutoAddPolicy())
        return client

    def _connect_kwargs(self, connection: Connection) -> dict:
        kwargs = {
            "hostname": connection.host,
            "port": connection.port or 22,
            "username": connection.username,
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if connection.credential_ref:
            kwargs["key_filename"] = connection.credential_ref
        return kwargs

    def _verify_fingerprint(self, client: paramiko.SSHClient, connection: Connection) -> None:
        expected = (connection.host_fingerprint or "").strip()
        if not expected:
            if get_settings().ssh_strict_host_key_checking:
                raise ValueError("SSH host fingerprint is required in strict mode")
            return
        transport = client.get_transport()
        key = transport.get_remote_server_key() if transport else None
        if not key:
            raise ValueError("Unable to read SSH host key")
        actual = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != expected:
            raise ValueError(f"SSH host fingerprint mismatch: expected {expected}, got {actual}")
