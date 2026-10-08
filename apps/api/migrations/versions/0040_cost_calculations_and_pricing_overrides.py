"""Cost: per-call cost lines (derived) and user price overrides (ADR-040).

`cost_calculations` is rebuilt by the summarizer for a run (delete + insert), so it needs no history. The
runtime role gets full DML through the default privileges of revision 0007. Both tables are tenant-keyed with
composite foreign keys like every other table.

Revision ID: 0040
Revises: 0009
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0009"
branch_labels = None
depends_on = None

SOURCES = "'provider_reported', 'estimated', 'client_estimate', 'unpriced'"


def _money(name: str, *, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Numeric(20, 9), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "cost_calculations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("agent_slug", sa.Text(), nullable=False),
        sa.Column("span_id", sa.UUID(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("pricing_origin", sa.Text(), nullable=True),
        _money("input_cost"),
        _money("output_cost"),
        _money("cached_cost"),
        _money("request_cost"),
        _money("estimated_total"),
        _money("reported_total"),
        _money("client_total"),
        _money("total", nullable=False),
        sa.Column("is_retry", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "event_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(f"source IN ({SOURCES})", name="ck_cost_calculations_source"),
    )
    op.create_index("ix_cost_run", "cost_calculations", ["workspace_id", "run_id"])
    op.create_index(
        "ix_cost_project_started",
        "cost_calculations",
        ["workspace_id", "project_id", "run_started_at"],
    )
    op.create_table(
        "pricing_overrides",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model_pattern", sa.Text(), nullable=False),
        _money("input_per_million", nullable=False),
        _money("output_per_million", nullable=False),
        _money("cached_input_per_million"),
        sa.Column("request_price", sa.Numeric(20, 9), server_default="0", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
        sa.CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0 AND request_price >= 0 "
            "AND (cached_input_per_million IS NULL OR cached_input_per_million >= 0)",
            name="ck_pricing_overrides_nonnegative",
        ),
    )
    op.create_index(
        "ix_pricing_overrides_workspace", "pricing_overrides", ["workspace_id", "project_id"]
    )


def downgrade() -> None:
    op.drop_table("pricing_overrides")
    op.drop_table("cost_calculations")
