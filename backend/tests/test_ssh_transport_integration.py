import base64
import hashlib
import socket
from threading import Event, Thread
import time
from types import SimpleNamespace

import paramiko

from app.runtime.transports.ssh import SSHTransport


class PublicKeyServer(paramiko.ServerInterface):
    def __init__(self, allowed_key: paramiko.PKey) -> None:
        self.allowed_key = allowed_key
        self.command = ""
        self.command_received = Event()

    def get_allowed_auths(self, username: str) -> str:
        del username
        return "publickey"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        if username == "opsagent" and key.asbytes() == self.allowed_key.asbytes():
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        del chanid
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command: bytes) -> bool:
        del channel
        self.command = command.decode("utf-8", errors="replace")
        self.command_received.set()
        return True


class LocalSSHServer:
    def __init__(self, host_key: paramiko.PKey, allowed_key: paramiko.PKey, response: str) -> None:
        self.host_key = host_key
        self.allowed_key = allowed_key
        self.response = response
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.transport: paramiko.Transport | None = None
        self.thread = Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.listener.close()
        if self.transport:
            self.transport.close()
        self.thread.join(timeout=4)

    def _serve(self) -> None:
        try:
            client, _ = self.listener.accept()
            self.transport = paramiko.Transport(client)
            self.transport.add_server_key(self.host_key)
            server = PublicKeyServer(self.allowed_key)
            self.transport.start_server(server=server)
            channel = self.transport.accept(3)
            if channel is None or not server.command_received.wait(3):
                return
            if self.response == "timeout":
                time.sleep(2)
                return
            if self.response == "failed":
                channel.sendall_stderr(b"synthetic failure")
                channel.send_exit_status(7)
                return
            if self.response == "large":
                channel.sendall(b"x" * 70_000)
                channel.sendall_stderr(b"y" * 20_000)
                channel.send_exit_status(0)
                return
            channel.sendall(b"ssh integration ok")
            channel.send_exit_status(0)
        except (OSError, EOFError, paramiko.SSHException):
            return


def _fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _connection(server: LocalSSHServer, key_path: str, fingerprint: str | None = None):
    return SimpleNamespace(
        host="127.0.0.1",
        port=server.port,
        username="opsagent",
        credential_ref=key_path,
        host_fingerprint=fingerprint or _fingerprint(server.host_key),
    )


def _private_key(tmp_path, name: str) -> tuple[paramiko.RSAKey, str]:
    key = paramiko.RSAKey.generate(2048)
    path = tmp_path / name
    key.write_private_key_file(str(path))
    path.chmod(0o600)
    return key, str(path)


def test_real_ssh_success_nonzero_and_output_limits(tmp_path):
    host_key = paramiko.RSAKey.generate(2048)
    user_key, key_path = _private_key(tmp_path, "user-key")
    environment = SimpleNamespace(workdir=None)
    with LocalSSHServer(host_key, user_key, "success") as server:
        result = SSHTransport().execute(_connection(server, key_path), environment, ["probe"], timeout_seconds=3)
        assert result.status == "success" and result.exit_code == 0
        assert result.stdout == "ssh integration ok"
    with LocalSSHServer(host_key, user_key, "failed") as server:
        result = SSHTransport().execute(_connection(server, key_path), environment, ["probe"], timeout_seconds=3)
        assert result.status == "failed" and result.exit_code == 7
        assert "synthetic failure" in result.stderr
    with LocalSSHServer(host_key, user_key, "large") as server:
        result = SSHTransport().execute(_connection(server, key_path), environment, ["probe"], timeout_seconds=3)
        assert result.status == "success" and result.truncated is True
        assert len(result.stdout) <= 65_536 and len(result.stderr) <= 16_384


def test_real_ssh_rejects_wrong_key_and_host_fingerprint(tmp_path):
    host_key = paramiko.RSAKey.generate(2048)
    allowed_key, allowed_path = _private_key(tmp_path, "allowed-key")
    _, wrong_path = _private_key(tmp_path, "wrong-key")
    environment = SimpleNamespace(workdir=None)
    with LocalSSHServer(host_key, allowed_key, "success") as server:
        result = SSHTransport().execute(_connection(server, wrong_path), environment, ["probe"], timeout_seconds=3)
        assert result.status == "failed" and result.exit_code is None
    with LocalSSHServer(host_key, allowed_key, "success") as server:
        result = SSHTransport().execute(
            _connection(server, allowed_path, "SHA256:" + "A" * 43),
            environment,
            ["probe"],
            timeout_seconds=3,
        )
        assert result.status == "failed"
        assert "fingerprint" in result.stderr.lower() or "host key" in result.stderr.lower()


def test_real_ssh_timeout_and_unreachable_host(tmp_path):
    host_key = paramiko.RSAKey.generate(2048)
    user_key, key_path = _private_key(tmp_path, "timeout-key")
    environment = SimpleNamespace(workdir=None)
    with LocalSSHServer(host_key, user_key, "timeout") as server:
        result = SSHTransport().execute(_connection(server, key_path), environment, ["probe"], timeout_seconds=1)
        assert result.status == "failed" and result.exit_code is None
        assert "timed out" in result.stderr.lower()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    unused_port = listener.getsockname()[1]
    listener.close()
    connection = SimpleNamespace(
        host="127.0.0.1",
        port=unused_port,
        username="opsagent",
        credential_ref=key_path,
        host_fingerprint=_fingerprint(host_key),
    )
    result = SSHTransport().execute(connection, environment, ["probe"], timeout_seconds=1)
    assert result.status == "failed" and result.exit_code is None
