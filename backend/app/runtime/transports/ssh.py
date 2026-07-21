from dataclasses import dataclass
import base64
import hashlib
import os
from pathlib import PurePosixPath
import shlex
import posixpath
import socket
import stat
import time
from typing import Callable
from uuid import uuid4

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
    error_code: str | None = None


class SSHTransport:
    def __init__(self, *, reuse_connections: bool = False) -> None:
        self.reuse_connections = reuse_connections
        self._shared_clients: dict[tuple[str, int, str, str, str], paramiko.SSHClient] = {}

    def close(self) -> None:
        clients = list(self._shared_clients.values())
        self._shared_clients.clear()
        for client in clients:
            client.close()

    def test_connection(self, connection: Connection) -> tuple[bool, str]:
        credential_error = self._credential_error(connection)
        if credential_error:
            return False, credential_error[1]
        client = self._client(connection)
        try:
            client.connect(**self._connect_kwargs(connection))
            self._verify_fingerprint(client, connection)
            return True, "SSH connection succeeded"
        except Exception as exc:  # noqa: BLE001
            return False, self._connection_error(exc)[1]
        finally:
            client.close()

    def execute(
        self,
        connection: Connection,
        environment: Environment,
        argv: list[str],
        *,
        timeout_seconds: int = 20,
        cancel_check: Callable[[], bool] | None = None,
    ) -> TransportResult:
        if not argv or any("\x00" in item or "\n" in item for item in argv):
            return TransportResult("failed", None, "", "Invalid command arguments", 0)
        start = time.monotonic()
        credential_error = self._credential_error(connection)
        if credential_error:
            error_code, message = credential_error
            return TransportResult("failed", None, "", message, 0, error_code=error_code)
        client: paramiko.SSHClient | None = None
        close_after_command = False
        try:
            client, close_after_command = self._acquire_client(connection)
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
                if cancel_check and cancel_check():
                    channel.close()
                    return TransportResult("cancelled", None, "", "Command cancelled", int((time.monotonic() - start) * 1000))
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
                    if not close_after_command:
                        self._discard_shared_client(connection, client)
                    out, _ = truncate_text(out_buffer.decode("utf-8", errors="replace"), 65536)
                    err, _ = truncate_text(err_buffer.decode("utf-8", errors="replace"), 16384)
                    return TransportResult(
                        "failed",
                        None,
                        out,
                        (err + "\nCommand timed out").strip(),
                        int((time.monotonic() - start) * 1000),
                        True,
                        "ssh_command_timeout",
                    )
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
            if client is not None and not close_after_command:
                self._discard_shared_client(connection, client)
            error_code, message = self._connection_error(exc)
            return TransportResult(
                "failed",
                None,
                "",
                message,
                int((time.monotonic() - start) * 1000),
                error_code=error_code,
            )
        finally:
            if client is not None and close_after_command:
                client.close()

    def read_file(
        self,
        connection: Connection,
        environment: Environment,
        relative_path: str,
        *,
        timeout_seconds: int = 20,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
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
                sftp.get_channel().settimeout(timeout_seconds)
                target = self._safe_remote_target(sftp, environment.workdir, relative_path, allow_missing_leaf=False)
                with sftp.open(target, "r") as handle:
                    content = bytearray()
                    while len(content) <= 2_000_000:
                        if cancel_check and cancel_check():
                            raise RuntimeError("Collector cancelled")
                        chunk = handle.read(min(65_536, 2_000_001 - len(content)))
                        if not chunk:
                            break
                        content.extend(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
            if len(content) > 2_000_000:
                raise ValueError("Context source file exceeds 2 MB")
            return bytes(content).decode("utf-8")
        finally:
            client.close()

    def write_registered_file(self, connection: Connection, environment: Environment, relative_path: str, content: str, *, backup_path: str | None = None) -> TransportResult:
        if not environment.workdir or len(content.encode("utf-8")) > 2_000_000:
            return TransportResult("failed", None, "", "Invalid workdir or registered content exceeds 2 MB", 0)
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return TransportResult("failed", None, "", "File path must stay inside the environment workdir", 0)
        client = self._client(connection); start = time.monotonic()
        try:
            client.connect(**self._connect_kwargs(connection)); self._verify_fingerprint(client, connection)
            with client.open_sftp() as sftp:
                target = self._safe_remote_target(sftp, environment.workdir, relative_path, allow_missing_leaf=True)
                temporary = f"{target}.ops-agent.{uuid4().hex}.tmp"
                backup = self._safe_remote_target(sftp, environment.workdir, backup_path, allow_missing_leaf=True) if backup_path else None
                target_existed = True
                try:
                    sftp.lstat(target)
                except OSError as exc:
                    if getattr(exc, "errno", None) == 2:
                        target_existed = False
                    else:
                        raise
                backup_moved = False
                try:
                    if backup and target_existed:
                        sftp.posix_rename(target, backup)
                        backup_moved = True
                    with sftp.open(temporary, "w") as handle:
                        handle.write(content)
                    sftp.posix_rename(temporary, target)
                except Exception as write_error:
                    cleanup_error = None
                    try:
                        sftp.remove(temporary)
                    except OSError as cleanup_exc:
                        if getattr(cleanup_exc, "errno", None) != 2:
                            cleanup_error = cleanup_exc
                    if backup_moved:
                        try:
                            sftp.remove(target)
                        except OSError as cleanup_exc:
                            if getattr(cleanup_exc, "errno", None) != 2:
                                cleanup_error = cleanup_error or cleanup_exc
                        try:
                            sftp.posix_rename(backup, target)
                        except Exception as restore_error:
                            raise RuntimeError(f"Registered file write failed and original restore also failed: {restore_error}") from write_error
                    if cleanup_error:
                        raise RuntimeError(f"Registered file write failed and temporary cleanup failed: {cleanup_error}") from write_error
                    raise write_error
            return TransportResult("success", 0, backup_path or "", "", int((time.monotonic() - start) * 1000))
        except Exception as exc:  # noqa: BLE001
            return TransportResult("failed", None, "", f"Registered file update failed: {exc}", int((time.monotonic() - start) * 1000))
        finally:
            client.close()

    def restore_registered_file(self, connection: Connection, environment: Environment, relative_path: str, backup_path: str) -> TransportResult:
        client = self._client(connection); start = time.monotonic()
        try:
            client.connect(**self._connect_kwargs(connection)); self._verify_fingerprint(client, connection)
            with client.open_sftp() as sftp:
                target = self._safe_remote_target(sftp, environment.workdir, relative_path, allow_missing_leaf=True)
                backup = self._safe_remote_target(sftp, environment.workdir, backup_path, allow_missing_leaf=True)
                try:
                    sftp.lstat(backup)
                except OSError as exc:
                    if getattr(exc, "errno", None) != 2:
                        raise
                    try:
                        sftp.remove(target)
                    except OSError as remove_exc:
                        if getattr(remove_exc, "errno", None) != 2:
                            raise
                else:
                    sftp.posix_rename(backup, target)
            return TransportResult("success", 0, "registered file restored", "", int((time.monotonic() - start) * 1000))
        except Exception as exc:  # noqa: BLE001
            return TransportResult("failed", None, "", f"Registered file rollback failed: {exc}", int((time.monotonic() - start) * 1000))
        finally:
            client.close()

    def remove_registered_backup(self, connection: Connection, environment: Environment, backup_path: str) -> None:
        client = self._client(connection)
        try:
            client.connect(**self._connect_kwargs(connection)); self._verify_fingerprint(client, connection)
            with client.open_sftp() as sftp:
                backup = self._safe_remote_target(sftp, environment.workdir, backup_path, allow_missing_leaf=True)
                try:
                    sftp.remove(backup)
                except OSError as exc:
                    if getattr(exc, "errno", None) != 2:
                        raise
        finally:
            client.close()


    def _client(self, connection: Connection) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        strict = get_settings().ssh_strict_host_key_checking
        if strict and connection.host_fingerprint:
            client.set_missing_host_key_policy(FingerprintPolicy(connection.host_fingerprint))
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy() if strict else paramiko.AutoAddPolicy())
        return client

    def _acquire_client(self, connection: Connection) -> tuple[paramiko.SSHClient, bool]:
        key = self._connection_key(connection)
        if self.reuse_connections:
            cached = self._shared_clients.get(key)
            transport = cached.get_transport() if cached else None
            if cached and transport and transport.is_active():
                return cached, False
            if cached:
                cached.close()
                self._shared_clients.pop(key, None)

        client = self._client(connection)
        try:
            client.connect(**self._connect_kwargs(connection))
            self._verify_fingerprint(client, connection)
        except Exception:
            client.close()
            raise
        if self.reuse_connections:
            self._shared_clients[key] = client
            return client, False
        return client, True

    @staticmethod
    def _connection_key(connection: Connection) -> tuple[str, int, str, str, str]:
        return (
            str(connection.host or ""),
            int(connection.port or 22),
            str(connection.username or ""),
            str(connection.credential_ref or ""),
            str(connection.host_fingerprint or ""),
        )

    def _discard_shared_client(self, connection: Connection, client: paramiko.SSHClient) -> None:
        key = self._connection_key(connection)
        if self._shared_clients.get(key) is client:
            self._shared_clients.pop(key, None)
        client.close()

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

    @staticmethod
    def _credential_error(connection: Connection) -> tuple[str, str] | None:
        credential_ref = str(connection.credential_ref or "").strip()
        if not credential_ref:
            return "ssh_credential_not_configured", "项目尚未配置 SSH 私钥，无法连接目标服务器。"
        if not os.path.isfile(credential_ref):
            return (
                "ssh_credential_missing",
                "运行容器中没有找到 SSH 私钥。请重新创建 Backend 和 Worker 容器以恢复密钥挂载。",
            )
        if not os.access(credential_ref, os.R_OK):
            return "ssh_credential_unreadable", "运行容器无法读取 SSH 私钥，请检查密钥文件权限。"
        return None

    @staticmethod
    def _connection_error(exc: Exception) -> tuple[str, str]:
        detail = str(exc)
        lowered = detail.lower()
        if isinstance(exc, paramiko.AuthenticationException):
            return "ssh_authentication_failed", "SSH 身份验证失败，请检查用户名、私钥和 authorized_keys 配置。"
        if isinstance(exc, paramiko.BadHostKeyException) or "fingerprint" in lowered or "host key" in lowered:
            return "ssh_host_key_mismatch", "SSH host fingerprint 校验失败，请确认目标服务器身份和已登记指纹。"
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return "ssh_connection_timeout", "连接目标服务器超时，请检查 SSH 地址、端口和网络连通性。"
        if isinstance(exc, (paramiko.ssh_exception.NoValidConnectionsError, ConnectionError, OSError)):
            return "ssh_connection_failed", "无法连接目标服务器，请检查 SSH 地址、端口和服务状态。"
        return "ssh_execution_failed", f"SSH 执行失败：{detail}"

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

    @staticmethod
    def _safe_remote_target(sftp, workdir: str, relative_path: str, *, allow_missing_leaf: bool) -> str:
        root = sftp.normalize(workdir).rstrip("/") or "/"
        target = posixpath.normpath(posixpath.join(root, relative_path))
        if posixpath.commonpath([root, target]) != root:
            raise ValueError("File path escapes the environment workdir")
        current = root
        parts = PurePosixPath(posixpath.relpath(target, root)).parts
        for index, part in enumerate(parts):
            current = posixpath.join(current, part)
            try:
                attributes = sftp.lstat(current)
            except OSError as exc:
                if allow_missing_leaf and index == len(parts) - 1 and getattr(exc, "errno", None) == 2:
                    break
                raise
            if stat.S_ISLNK(attributes.st_mode):
                raise ValueError(f"Symbolic links are not allowed for managed files: {current}")
        return target


class FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: str) -> None:
        self.expected = expected.strip()

    def missing_host_key(self, client, hostname, key) -> None:
        del client
        actual = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != self.expected:
            raise paramiko.SSHException(f"SSH host fingerprint mismatch for {hostname}")
