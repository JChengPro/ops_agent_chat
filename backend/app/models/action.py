from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CapabilityVersion(Base):
    __tablename__ = "capability_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_capability_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    definition_hash: Mapped[str] = mapped_column(String(64))
    effect: Mapped[str] = mapped_column(String(20))
    default_risk_level: Mapped[str] = mapped_column(String(10))
    approval_mode: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(default=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','ready','waiting_for_approval','approved','executing','succeeded','failed','denied','needs_clarification','precheck_failed','precheck_changed','rejected','expired','cancelled','approval_invalid','verification_failed','verified','rolled_back','rollback_failed','execution_unknown')",
            name="ck_action_status",
        ),
        CheckConstraint("effect IN ('read','change')", name="ck_action_effect"),
        CheckConstraint("risk_level IN ('L0','L1','L2','L3')", name="ck_action_risk_level"),
        CheckConstraint("approval_mode IN ('never','always','conditional','forbidden')", name="ck_action_approval_mode"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("agent_steps.id"), nullable=True)
    capability_name: Mapped[str] = mapped_column(String(120), index=True)
    capability_version: Mapped[str] = mapped_column(String(40))
    capability_definition_hash: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(10), default="L3")
    approval_mode: Mapped[str] = mapped_column(String(30), default="forbidden")
    policy_version: Mapped[str] = mapped_column(String(40), default="legacy-unbound")
    config_revision: Mapped[str] = mapped_column(String(64), default="0" * 64)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True, index=True)
    target_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolved_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rollback_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    effect: Mapped[str] = mapped_column(String(20))
    action_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), default="proposed", index=True)
    execution_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    risk_level: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text)
    matched_policies_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(40), default="final-1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('pending','approved','rejected','expired','cancelled','invalidated')", name="ck_approval_decision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), unique=True, index=True)
    action_hash: Mapped[str] = mapped_column(String(64))
    requested_from: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    impact_summary: Mapped[str] = mapped_column(Text)
    risk_summary: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    executor_type: Mapped[str] = mapped_column(String(60))
    target_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stdout_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
