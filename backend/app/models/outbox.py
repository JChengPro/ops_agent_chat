from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RunOutbox(Base):
    __tablename__ = "agent_run_outbox"
    __table_args__ = (
        UniqueConstraint("run_id", "dispatch_version", name="uq_run_outbox_dispatch"),
        Index("ix_run_outbox_next_attempt", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    dispatch_version: Mapped[int] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
