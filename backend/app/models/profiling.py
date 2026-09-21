from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProfileSpan(Base):
    __tablename__ = "agent_run_profile_spans"
    __table_args__ = (Index("ix_profile_run_started", "run_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Logical references avoid coupling telemetry writes to business row locks.
    run_id: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(40))
    round_index: Mapped[int | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
