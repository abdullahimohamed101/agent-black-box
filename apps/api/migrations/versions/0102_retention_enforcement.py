"""Retention enforcement (Phase 19b step 2, ADR-160): the deleting marker, progress, dirty days.

* `runs.trace_state` (`available` | `deleting` | `expired`): readers say "trace expired" instead of
  rendering a half-deleted run; a partial index finds the runs the job left `deleting` so it can
  resume them. A NOT NULL column with a constant default is a catalog change, not a table rewrite.
* `retention_run_progress`: per run and tier, which backends are done (artifact store, search
  index, Postgres detail, run record, analytics). The job writes it; the application only reads.
* `analytics_dirty` (KI-057): the version of a workspace-day's pending analytics change.
* Grants of `abb_retention`, all explicit (its 0101 grants stay): `UPDATE (trace_state)` on runs and
  `UPDATE (bytes_stored)` on `project_artifact_usage` (columns, not tables), the progress table, the
  rollup tables it rebuilds after deleting (SELECT/INSERT/DELETE: the same statements the worker's
  refresh runs) and `analytics_dirty` (SELECT/DELETE, to prune old rows). Nothing else; the runtime
  role loses write access to what the job reports (the progress table).

Revision ID: 0102
Revises: 0101
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None

ROLE = "abb_retention"
RUNTIME = "abb_runtime"
ROLLUPS = (
    "analytics_runs_daily",
    "analytics_cost_daily",
    "analytics_spans_daily",
    "analytics_latency_daily",
    "analytics_top_runs",
)
STEPS = ("artifacts_done", "search_done", "detail_done", "record_done", "analytics_done")


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("trace_state", sa.Text(), server_default="available", nullable=False)
    )
    op.create_check_constraint(
        "ck_runs_trace_state", "runs", "trace_state IN ('available', 'deleting', 'expired')"
    )
    op.create_index(
        "ix_runs_trace_deleting",
        "runs",
        ["workspace_id", "id"],
        postgresql_where=sa.text("trace_state = 'deleting'"),
    )
    op.create_table(
        "retention_run_progress",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID()),
        sa.Column("started_day", sa.Date()),
        *[
            sa.Column(s, sa.Boolean(), server_default=sa.text("false"), nullable=False)
            for s in STEPS
        ],
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_code", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "tier"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "tier IN ('artifacts', 'trace', 'run')", name="ck_retention_progress_tier"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_retention_progress_attempts"),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('artifact_store', 'database', 'timeout', 'internal')",
            name="ck_retention_progress_failure",
        ),
        sa.CheckConstraint(
            "tier = 'artifacts' OR started_day IS NOT NULL", name="ck_retention_progress_day"
        ),
    )
    op.create_index(
        "ix_retention_progress_open",
        "retention_run_progress",
        ["workspace_id", "tier", "created_at"],
    )
    op.create_table(
        "analytics_dirty",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("refreshed_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "day"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint("refreshed_version <= version", name="ck_analytics_dirty_versions"),
    )

    # The application reads what the job records and never writes it.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE retention_run_progress FROM {RUNTIME}")

    op.execute(f"GRANT UPDATE (trace_state) ON runs TO {ROLE}")
    op.execute(f"GRANT UPDATE (bytes_stored) ON project_artifact_usage TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON retention_run_progress TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {', '.join(ROLLUPS)} TO {ROLE}")
    op.execute(f"GRANT SELECT, DELETE ON analytics_dirty TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (trace_state) ON runs FROM {ROLE}")
    op.execute(f"REVOKE UPDATE (bytes_stored) ON project_artifact_usage FROM {ROLE}")
    op.execute(f"REVOKE ALL ON retention_run_progress, analytics_dirty FROM {ROLE}")
    op.execute(f"REVOKE ALL ON {', '.join(ROLLUPS)} FROM {ROLE}")
    op.drop_table("analytics_dirty")
    op.drop_index("ix_retention_progress_open", table_name="retention_run_progress")
    op.drop_table("retention_run_progress")
    op.drop_index("ix_runs_trace_deleting", table_name="runs")
    op.drop_constraint("ck_runs_trace_state", "runs", type_="check")
    op.drop_column("runs", "trace_state")
