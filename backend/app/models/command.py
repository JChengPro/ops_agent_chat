from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CommandPlan(Base):
    __tablename__ = "command_plans"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True)
    generated_by: Mapped[str] = mapped_column(String(80), default="command_agent")
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(60), default="generated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommandRun(Base):
    __tablename__ = "command_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    command_plan_id: Mapped[int | None] = mapped_column(ForeignKey("command_plans.id"), nullable=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), index=True)
    command: Mapped[str] = mapped_column(Text)
    cwd: Mapped[str] = mapped_column(String(500))
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="L0")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    stdout_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout_truncated: Mapped[bool] = mapped_column(default=False)
    stderr_truncated: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    ruleguard_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

