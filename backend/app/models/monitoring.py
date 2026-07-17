from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MonitorEvent(Base):
    __tablename__ = "monitor_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','remediating','remediated','resolved','remediation_failed')",
            name="ck_monitor_event_status",
        ),
        CheckConstraint("severity IN ('info','warning','critical')", name="ck_monitor_event_severity"),
        Index(
            "uq_monitor_event_active_issue",
            "environment_id",
            "service_name",
            "issue_type",
            unique=True,
            postgresql_where=text("status IN ('open','remediating','remediation_failed')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    service_name: Mapped[str] = mapped_column(String(255), index=True)
    issue_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurrence_count: Mapped[int] = mapped_column(default=1)
    remediation_action_id: Mapped[str | None] = mapped_column(ForeignKey("actions.id"), nullable=True)
    diagnostic_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, unique=True, index=True
    )
    diagnosis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnosed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
