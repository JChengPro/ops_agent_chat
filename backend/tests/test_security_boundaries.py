from app.llm.gateway import SYSTEM_PROMPT
from app.runtime.adapters.registered import RegisteredConfigAdapter
from app.runtime.adapters.http import HttpAdapter
from app.runtime.transports.ssh import TransportResult
from app.utils.redaction import redact_secrets, truncate_text
from app.utils.public_config import public_config


class Config:
    workdir = "/srv/app"
    config_json = {"registered_config_changes": {"safe-change": {"path": "config/app.yml", "content": "enabled: true\n", "current_sha256": "invalid-on-purpose"}}}


class FakeTransport:
    content = "enabled: false\n"

    def read_file(self, connection, environment, path):
        return self.content

    def write_registered_file(self, connection, environment, path, content):
        self.content = content
        return TransportResult("success", 0, "ok", "", 1)


def test_registered_config_rejects_unregistered_and_changed_precondition():
    adapter = RegisteredConfigAdapter(FakeTransport())
    missing = adapter.execute("config.update_registered", {"change": "not-registered"}, object(), Config())
    assert missing.status == "failed"
    changed = adapter.execute("config.update_registered", {"change": "safe-change"}, object(), Config())
    assert changed.status == "failed"
    assert "precondition" in changed.summary.lower()


def test_secret_redaction_and_output_limit():
    value = redact_secrets("api_key=top-secret password=hunter2 Authorization: Bearer abc.def.ghi https://me:pass@example.test sk-1234567890abcdefghijkl")
    assert all(secret not in value for secret in ("top-secret", "hunter2", "abc.def.ghi", ":pass@", "sk-1234567890abcdefghijkl"))
    shortened, truncated = truncate_text("x" * 100, 10)
    assert truncated and shortened.startswith("x" * 10)


def test_public_config_hides_registered_file_content_and_nested_secrets():
    safe = public_config({"registered_config_changes": {"change": {"path": "app.yml", "content": "password: secret"}}, "nested": {"api_key": "secret"}})
    assert safe["registered_config_changes"]["change"]["content_configured"] is True
    assert safe["nested"]["api_key_configured"] is True
    assert "password: secret" not in str(safe)


def test_tool_output_is_explicitly_untrusted_in_system_prompt():
    assert "untrusted data" in SYSTEM_PROMPT
    assert "Never invent a tool" in SYSTEM_PROMPT


def test_health_adapter_blocks_link_local_metadata_address(monkeypatch):
    class Environment:
        config_json = {"health_endpoints": {"default": "http://metadata.internal/latest/meta-data"}}

    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("169.254.169.254", 80))])
    result = HttpAdapter().execute({"endpoint": "default"}, Environment())
    assert result.status == "failed"
    assert "forbidden" in result.summary.lower()
