from typing import Any

from pydantic import BaseModel


class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    auth_type: str
    status: str

    model_config = {"from_attributes": True}


class ServerCreate(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    auth_type: str = "key"
    private_key_ref: str | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    deploy_type: str
    workdir: str
    compose_file: str | None = None
    health_url: str | None = None
    server_id: int
    allowed_container_prefixes: list[str]
    known_services: list[str]
    settings_json: dict[str, Any]

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    server_id: int
    deploy_type: str = "docker_compose"
    workdir: str
    compose_file: str | None = None
    health_url: str | None = None
    allowed_container_prefixes: list[str] = []
    known_services: list[str] = []
