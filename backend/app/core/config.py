from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str = Field(default="change-me", alias="APP_SECRET_KEY")
    jwt_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str = "postgresql+psycopg://opsagent:opsagent_password@localhost:5432/ops_agent_chat"

    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "DEEPSEEK_API_KEY"),
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("LLM_BASE_URL", "DEEPSEEK_BASE_URL"),
    )
    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    llm_model: str = Field(default="deepseek-v4-pro", alias="LLM_MODEL")
    llm_allowed_base_urls: str = Field(
        default="https://api.deepseek.com,https://api.openai.com/v1",
        alias="LLM_ALLOWED_BASE_URLS",
    )
    llm_credential_encryption_key: str = Field(default="", alias="LLM_CREDENTIAL_ENCRYPTION_KEY")
    llm_reasoning_effort: str = Field(default="high", alias="LLM_REASONING_EFFORT")
    llm_thinking_enabled: bool = Field(default=True, alias="LLM_THINKING_ENABLED")
    llm_timeout_seconds: int = Field(default=90, alias="LLM_TIMEOUT_SECONDS")
    agent_timeout_seconds: int = Field(default=300, alias="AGENT_TIMEOUT_SECONDS")
    agent_context_max_chars: int = Field(default=60000, alias="AGENT_CONTEXT_MAX_CHARS")
    monitor_interval_seconds: int = Field(default=15, ge=5, alias="MONITOR_INTERVAL_SECONDS")

    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me-before-running"
    registration_enabled: bool = Field(default=True, alias="REGISTRATION_ENABLED")
    registration_invite_code: str = Field(default="", alias="REGISTRATION_INVITE_CODE")

    videohub_project_name: str = "VideoHub"
    videohub_deploy_type: str = "docker_compose"
    videohub_workdir: str = "/home/jcheng/Golang/feedsystem_video_go"
    videohub_compose_file: str = "docker-compose.yml"
    videohub_health_url: str = "http://127.0.0.1:8080/health"
    videohub_ssh_host: str = "127.0.0.1"
    videohub_ssh_port: int = 22
    videohub_ssh_username: str = "opsagent"
    videohub_ssh_key_path: str = ""
    videohub_ssh_host_fingerprint: str = ""
    ssh_strict_host_key_checking: bool = Field(default=True, alias="SSH_STRICT_HOST_KEY_CHECKING")

    knowledge_root: Path = Path("docs/knowledge")

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.app_env.lower() in {"prod", "production"}:
            problems = []
            if self.app_secret_key in {"change-me", "replace-with-a-long-random-string"} or len(self.app_secret_key) < 32:
                problems.append("APP_SECRET_KEY must be a non-default value of at least 32 characters")
            if self.admin_password == "change-me-before-running":
                problems.append("ADMIN_PASSWORD must not use the development default")
            if self.registration_enabled and (
                len(self.registration_invite_code) < 16
                or self.registration_invite_code in {"change-me", "replace-with-a-registration-code"}
            ):
                problems.append("REGISTRATION_INVITE_CODE must be a non-default value of at least 16 characters when registration is enabled")
            if "opsagent_password" in self.database_url:
                problems.append("DATABASE_URL must not use the development password")
            if not self.ssh_strict_host_key_checking:
                problems.append("SSH_STRICT_HOST_KEY_CHECKING must be enabled")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def checkpoint_database_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def llm_configured(self) -> bool:
        value = self.llm_api_key.strip()
        return bool(
            value
            and value
            not in {
                "replace-with-your-model-api-key",
                "replace-with-your-deepseek-api-key",
                "replace-with-api-key",
                "your-api-key",
            }
        )

    @property
    def llm_allowed_base_url_list(self) -> list[str]:
        configured = [item.strip().rstrip("/") for item in self.llm_allowed_base_urls.split(",") if item.strip()]
        current = self.llm_base_url.strip().rstrip("/")
        return list(dict.fromkeys([*configured, current]))


@lru_cache
def get_settings() -> Settings:
    return Settings()
