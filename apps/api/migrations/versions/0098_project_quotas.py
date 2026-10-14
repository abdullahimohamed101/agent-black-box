"""Project quotas (Phase 19a step 3, ADR-154): overrides, daily usage and artifact bytes.

Three additive tables, all keyed by `(workspace_id, project_id)` with a composite foreign key to
`projects` (INV-3, and deleting a project removes its counters):

* `project_quota_overrides`: per-project overrides of the environment defaults; NULL = default.
* `project_usage_daily`: runs created and events accepted on one UTC day, maintained in the
  ingestion transaction. Counting starts at this migration: activity from before it is not in the
  first day's figures (an undercount of at most one day's pre-upgrade traffic).
* `project_artifact_usage`: total stored artifact bytes, backfilled from `artifacts` here so
  existing data counts against the quota from the first request.

Revision ID: 0098
Revises: 0097
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None

_FK = (["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"])


def upgrade() -> None:
    op.create_table(
        "project_quota_overrides",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("max_agents", sa.Integer()),
        sa.Column("max_runs_per_day", sa.Integer()),
        sa.Column("max_events_per_day", sa.BigInteger()),
        sa.Column("max_artifact_bytes", sa.BigInteger()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "project_id"),
        sa.ForeignKeyConstraint(*_FK, ondelete="CASCADE"),
        sa.CheckConstraint("max_agents BETWEEN 1 AND 1000000", name="ck_quota_overrides_agents"),
        sa.CheckConstraint(
            "max_runs_per_day BETWEEN 1 AND 100000000", name="ck_quota_overrides_runs"
        ),
        sa.CheckConstraint(
            "max_events_per_day BETWEEN 1 AND 100000000000", name="ck_quota_overrides_events"
        ),
        sa.CheckConstraint(
            "max_artifact_bytes BETWEEN 1 AND 1125899906842624",
            name="ck_quota_overrides_artifacts",
        ),
        sa.CheckConstraint("char_length(updated_by) <= 128", name="ck_quota_overrides_updated_by"),
    )
    op.create_table(
        "project_usage_daily",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("runs_created", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("events_accepted", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "project_id", "day"),
        sa.ForeignKeyConstraint(*_FK, ondelete="CASCADE"),
        sa.CheckConstraint(
            "runs_created >= 0 AND events_accepted >= 0", name="ck_usage_daily_counts"
        ),
    )
    op.create_table(
        "project_artifact_usage",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("bytes_stored", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "project_id"),
        sa.ForeignKeyConstraint(*_FK, ondelete="CASCADE"),
        sa.CheckConstraint("bytes_stored >= 0", name="ck_artifact_usage_bytes"),
    )
    # Existing artifacts count from the first request after the upgrade.
    op.execute(
        """
        INSERT INTO project_artifact_usage (workspace_id, project_id, bytes_stored)
        SELECT workspace_id, project_id, coalesce(sum(size_bytes), 0) FROM artifacts
        GROUP BY workspace_id, project_id
        """
    )


def downgrade() -> None:
    op.drop_table("project_artifact_usage")
    op.drop_table("project_usage_daily")
    op.drop_table("project_quota_overrides")
