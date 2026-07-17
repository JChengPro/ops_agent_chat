"""harden state sources and jobs

Revision ID: c4e8b1f6a205
Revises: b8a4d2e7f913
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8b1f6a205"
down_revision: Union[str, None] = "b8a4d2e7f913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("client_request_id", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_agent_run_client_request", "agent_runs", ["user_id", "session_id", "client_request_id"])

    op.add_column("approvals", sa.Column("decided_by", sa.Integer(), nullable=True))
    op.add_column("approvals", sa.Column("reason_code", sa.String(length=80), nullable=True))
    op.add_column("approvals", sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_approvals_decided_by_users", "approvals", "users", ["decided_by"], ["id"])

    op.execute("UPDATE collector_runs SET status='completed' WHERE status='success'")
    op.add_column("collector_runs", sa.Column("requested_by", sa.Integer(), nullable=True))
    op.add_column("collector_runs", sa.Column("lease_owner", sa.String(length=120), nullable=True))
    op.add_column("collector_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("collector_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("collector_runs", "started_at", existing_type=sa.DateTime(timezone=True), nullable=True, server_default=None)
    op.create_foreign_key("fk_collector_runs_requested_by_users", "collector_runs", "users", ["requested_by"], ["id"])
    op.create_index(op.f("ix_collector_runs_requested_by"), "collector_runs", ["requested_by"], unique=False)
    op.create_index(op.f("ix_collector_runs_status"), "collector_runs", ["status"], unique=False)
    op.create_index(op.f("ix_collector_runs_lease_owner"), "collector_runs", ["lease_owner"], unique=False)
    op.create_index(op.f("ix_collector_runs_lease_expires_at"), "collector_runs", ["lease_expires_at"], unique=False)
    op.execute(
        """
        UPDATE agent_runs
        SET status='failed', error_code='LEGACY_STATUS_INVALID',
            error_message='Legacy run status was not recognized during the state-machine migration',
            completed_at=COALESCE(completed_at, now())
        WHERE status NOT IN ('created','queued','running','waiting_for_approval','completed','failed','cancelled')
        """
    )
    op.execute(
        """
        UPDATE actions
        SET status='execution_unknown', execution_finished_at=COALESCE(execution_finished_at, now())
        WHERE status NOT IN ('proposed','ready','waiting_for_approval','approved','executing','succeeded','failed','denied','needs_clarification','precheck_failed','precheck_changed','rejected','expired','cancelled','approval_invalid','verification_failed','verified','rolled_back','rollback_failed','execution_unknown')
        """
    )
    op.execute(
        """
        UPDATE approvals
        SET decision='invalidated', reason_code='LEGACY_DECISION_INVALID', decided_at=COALESCE(decided_at, now())
        WHERE decision NOT IN ('pending','approved','rejected','expired','cancelled','invalidated')
        """
    )
    op.execute("UPDATE collector_runs SET status='failed', error_message=COALESCE(error_message, 'Legacy collector status was not recognized') WHERE status NOT IN ('queued','running','completed','failed','cancelled')")
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY environment_id, collector_name ORDER BY created_at DESC, id DESC
            ) AS rn
            FROM collector_runs
            WHERE status IN ('queued','running')
        )
        UPDATE collector_runs
        SET status='failed', error_message='Duplicate active collector was closed during migration',
            finished_at=COALESCE(finished_at, now()), lease_owner=NULL, lease_expires_at=NULL
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.execute("UPDATE experience_items SET trust_status='draft', verified_by=NULL, verified_at=NULL WHERE trust_status NOT IN ('draft','verified','rejected','archived')")
    for source_column in ("evidence_id", "context_source_id", "experience_item_id"):
        op.execute(
            f"""
            INSERT INTO evidence_claim_links (claim_id, {source_column})
            SELECT DISTINCT legacy.claim_id, legacy.{source_column}
            FROM evidence_claim_links AS legacy
            WHERE num_nonnulls(legacy.evidence_id, legacy.context_source_id, legacy.experience_item_id) > 1
              AND legacy.{source_column} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM evidence_claim_links AS existing
                  WHERE existing.claim_id = legacy.claim_id
                    AND existing.{source_column} = legacy.{source_column}
                    AND num_nonnulls(existing.evidence_id, existing.context_source_id, existing.experience_item_id) = 1
              )
            """
        )
    op.execute("DELETE FROM evidence_claim_links WHERE num_nonnulls(evidence_id, context_source_id, experience_item_id) <> 1")
    for source_column in ("evidence_id", "context_source_id", "experience_item_id"):
        op.execute(
            f"""
            DELETE FROM evidence_claim_links AS duplicate
            USING evidence_claim_links AS retained
            WHERE duplicate.id > retained.id
              AND duplicate.claim_id = retained.claim_id
              AND duplicate.{source_column} IS NOT NULL
              AND duplicate.{source_column} = retained.{source_column}
            """
        )

    op.create_index(
        "uq_collector_active_job",
        "collector_runs",
        ["environment_id", "collector_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )

    op.create_check_constraint("ck_agent_run_status", "agent_runs", "status IN ('created','queued','running','waiting_for_approval','completed','failed','cancelled')")
    op.create_check_constraint("ck_action_status", "actions", "status IN ('proposed','ready','waiting_for_approval','approved','executing','succeeded','failed','denied','needs_clarification','precheck_failed','precheck_changed','rejected','expired','cancelled','approval_invalid','verification_failed','verified','rolled_back','rollback_failed','execution_unknown')")
    op.create_check_constraint("ck_action_effect", "actions", "effect IN ('read','change')")
    op.create_check_constraint("ck_action_risk_level", "actions", "risk_level IN ('L0','L1','L2','L3')")
    op.create_check_constraint("ck_action_approval_mode", "actions", "approval_mode IN ('never','always','conditional','forbidden')")
    op.create_check_constraint("ck_approval_decision", "approvals", "decision IN ('pending','approved','rejected','expired','cancelled','invalidated')")
    op.create_check_constraint("ck_collector_run_status", "collector_runs", "status IN ('queued','running','completed','failed','cancelled')")
    op.create_check_constraint("ck_experience_trust_status", "experience_items", "trust_status IN ('draft','verified','rejected','archived')")
    op.create_check_constraint("ck_claim_link_exactly_one_source", "evidence_claim_links", "num_nonnulls(evidence_id, context_source_id, experience_item_id) = 1")
    op.create_index("uq_claim_runtime_evidence", "evidence_claim_links", ["claim_id", "evidence_id"], unique=True, postgresql_where=sa.text("evidence_id IS NOT NULL"))
    op.create_index("uq_claim_context_source", "evidence_claim_links", ["claim_id", "context_source_id"], unique=True, postgresql_where=sa.text("context_source_id IS NOT NULL"))
    op.create_index("uq_claim_experience_item", "evidence_claim_links", ["claim_id", "experience_item_id"], unique=True, postgresql_where=sa.text("experience_item_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_claim_experience_item", table_name="evidence_claim_links")
    op.drop_index("uq_claim_context_source", table_name="evidence_claim_links")
    op.drop_index("uq_claim_runtime_evidence", table_name="evidence_claim_links")
    op.drop_constraint("ck_claim_link_exactly_one_source", "evidence_claim_links", type_="check")
    op.drop_constraint("ck_experience_trust_status", "experience_items", type_="check")
    op.drop_constraint("ck_collector_run_status", "collector_runs", type_="check")
    op.drop_constraint("ck_approval_decision", "approvals", type_="check")
    op.drop_constraint("ck_action_approval_mode", "actions", type_="check")
    op.drop_constraint("ck_action_risk_level", "actions", type_="check")
    op.drop_constraint("ck_action_effect", "actions", type_="check")
    op.drop_constraint("ck_action_status", "actions", type_="check")
    op.drop_constraint("ck_agent_run_status", "agent_runs", type_="check")

    op.execute("UPDATE collector_runs SET status='success' WHERE status='completed'")
    op.drop_index("uq_collector_active_job", table_name="collector_runs")
    op.drop_index(op.f("ix_collector_runs_lease_expires_at"), table_name="collector_runs")
    op.drop_index(op.f("ix_collector_runs_lease_owner"), table_name="collector_runs")
    op.drop_index(op.f("ix_collector_runs_status"), table_name="collector_runs")
    op.drop_index(op.f("ix_collector_runs_requested_by"), table_name="collector_runs")
    op.drop_constraint("fk_collector_runs_requested_by_users", "collector_runs", type_="foreignkey")
    op.execute("UPDATE collector_runs SET started_at=COALESCE(started_at, created_at, now())")
    op.alter_column("collector_runs", "started_at", existing_type=sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
    op.drop_column("collector_runs", "cancel_requested_at")
    op.drop_column("collector_runs", "lease_expires_at")
    op.drop_column("collector_runs", "lease_owner")
    op.drop_column("collector_runs", "requested_by")

    op.drop_constraint("fk_approvals_decided_by_users", "approvals", type_="foreignkey")
    op.drop_column("approvals", "consumed_at")
    op.drop_column("approvals", "reason_code")
    op.drop_column("approvals", "decided_by")
    op.drop_constraint("uq_agent_run_client_request", "agent_runs", type_="unique")
    op.drop_column("agent_runs", "client_request_id")
