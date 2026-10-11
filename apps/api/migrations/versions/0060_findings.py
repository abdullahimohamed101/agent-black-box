"""Findings and run_findings (Phase 10, ADR-080): derived, rebuildable, metadata-only.

Additive. `findings` holds one row per detected finding of a run, `run_findings` one row per
analysed run (what the analysis saw). Both belong to the run (composite tenant key, ON DELETE
CASCADE like `spans`) and are rewritten by the `detect_findings` job; existing runs have none
until they are re-summarized or `rebuild-findings` runs. The application role keeps full DML (the
tables are derived, not append-only). The CHECKs are a backstop for the model's own bounds.

Revision ID: 0060
Revises: 0047
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060"
down_revision = "0047"
branch_labels = None
depends_on = None

KINDS = "'deterministic', 'probable'"
SEVERITIES = "'info', 'warning', 'critical'"


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("agent_slug", sa.Text(), nullable=True),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detector", sa.Text(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("finding_type", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("start_event_id", sa.UUID(), nullable=True),
        sa.Column("end_event_id", sa.UUID(), nullable=True),
        sa.Column("evidence_event_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "fingerprint", name="uq_findings_run_fingerprint"
        ),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_findings_kind"),
        sa.CheckConstraint(f"severity IN ({SEVERITIES})", name="ck_findings_severity"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_findings_confidence"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_findings_evidence_object"),
        sa.CheckConstraint("pg_column_size(evidence) < 16384", name="ck_findings_evidence_size"),
        sa.CheckConstraint(
            "cardinality(evidence_event_ids) <= 50", name="ck_findings_evidence_ids"
        ),
        sa.CheckConstraint(
            "char_length(detector) <= 64 AND char_length(detector_version) <= 32 "
            "AND char_length(finding_type) <= 64 AND char_length(fingerprint) = 64",
            name="ck_findings_text_sizes",
        ),
    )
    op.create_index("ix_findings_run", "findings", ["workspace_id", "run_id"])
    op.create_index(
        "ix_findings_project_started", "findings", ["workspace_id", "project_id", "run_started_at"]
    )
    op.create_table(
        "run_findings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("detectors_version", sa.Integer(), nullable=False),
        sa.Column("summary_version_seen", sa.Integer(), nullable=False),
        sa.Column("ordering_mode_seen", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count_seen", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("max_severity", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "run_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "ordering_mode_seen IN ('sequence', 'time')", name="ck_run_findings_ordering"
        ),
        sa.CheckConstraint(
            f"max_severity IS NULL OR max_severity IN ({SEVERITIES})",
            name="ck_run_findings_severity",
        ),
    )


def downgrade() -> None:
    op.drop_table("run_findings")
    op.drop_index("ix_findings_project_started", table_name="findings")
    op.drop_index("ix_findings_run", table_name="findings")
    op.drop_table("findings")
