from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deploy_type: Mapped[str] = mapped_column(String(80), default="docker_compose")
    workdir: Mapped[str] = mapped_column(String(500))
    compose_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    default_shell: Mapped[str] = mapped_column(String(80), default="/bin/bash")
    allowed_container_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list)
    known_services: Mapped[list[str]] = mapped_column(JSON, default=list)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

