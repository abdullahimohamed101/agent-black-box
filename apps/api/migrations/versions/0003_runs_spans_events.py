"""Runs, spans (derived) and the append-only events table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("agent_slug", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ordering_mode", sa.Text(), server_default="sequence", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordering_mode IN ('sequence', 'time')", name="ck_runs_ordering_mode"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING', 'WAITING_FOR_APPROVAL', 'SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'BLOCKED')",
            name="ck_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_runs_project_started",
        "runs",
        ["workspace_id", "project_id", sa.literal_column("started_at DESC"), "id"],
        unique=False,
    )
    op.create_index(
        "ix_runs_status_started",
        "runs",
        ["workspace_id", "status", sa.literal_column("started_at DESC"), "id"],
        unique=False,
    )
    op.create_table(
        "spans",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("parent_span_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("agent_slug", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_spans_run_parent", "spans", ["workspace_id", "run_id", "parent_span_id"], unique=False
    )
    op.create_table(
        "events",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("span_id", sa.UUID(), nullable=True),
        sa.Column("parent_span_id", sa.UUID(), nullable=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_ref", sa.Text(), nullable=True),
        sa.Column(
            "tags", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("sdk", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "event_id"),
    )
    op.create_index(
        "ix_events_run_sequence",
        "events",
        ["workspace_id", "run_id", "sequence", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_events_run_time",
        "events",
        ["workspace_id", "run_id", "occurred_at", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_events_type_time",
        "events",
        ["workspace_id", "event_type", sa.literal_column("occurred_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("spans")
    op.drop_table("runs")
