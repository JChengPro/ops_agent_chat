from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str = Field(default="change-me", alias="APP_SECRET_KEY")
    jwt_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str = "postgresql+psycopg://opsagent:opsagent_password@localhost:5432/ops_agent_chat"

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    llm_model: str = Field(default="deepseek-v4-pro", alias="LLM_MODEL")
    llm_reasoning_effort: str = Field(default="high", alias="LLM_REASONING_EFFORT")
    llm_thinking_enabled: bool = Field(default=True, alias="LLM_THINKING_ENABLED")

    embedding_provider: str = "placeholder"
    embedding_model: str = "placeholder"

    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me-before-running"

    videohub_project_name: str = "VideoHub"
    videohub_deploy_type: str = "docker_compose"
    videohub_workdir: str = "/home/jcheng/Golang/feedsystem_video_go"
    videohub_compose_file: str = "docker-compose.yml"
    videohub_health_url: str = "http://127.0.0.1:8080/health"
    videohub_ssh_host: str = "127.0.0.1"
    videohub_ssh_port: int = 22
    videohub_ssh_username: str = "opsagent"
    videohub_ssh_key_path: str = ""

    knowledge_root: Path = Path("docs/knowledge")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
