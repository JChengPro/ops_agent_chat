from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class ExperienceItem(Base):
    __tablename__ = "experience_items"
    __table_args__ = (
        CheckConstraint("trust_status IN ('draft','verified','rejected','archived')", name="ck_experience_trust_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    item_type: Mapped[str] = mapped_column(String(60), default="project_note")
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    applicable_entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_type: Mapped[str] = mapped_column(String(60), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    trust_status: Mapped[str] = mapped_column(String(40), default="draft")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ExperienceChunk(Base):
    __tablename__ = "experience_chunks"
    __table_args__ = (UniqueConstraint("experience_item_id", "chunk_key", name="uq_experience_chunk_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experience_item_id: Mapped[int] = mapped_column(ForeignKey("experience_items.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chunk_key: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str] = mapped_column(String(1000))
    heading_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    chunk_index: Mapped[int] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
