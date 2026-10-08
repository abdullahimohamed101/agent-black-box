"""Typed run figures and span window columns for analytics (ADR-043, measured on the Stage A dataset).

`runs` gains typed copies of the summary figures the dashboard aggregates (JSONB parsing of 700,000 summaries per
request was the measured cost); `spans` gains `project_id` and `run_started_at` so tool/model span analytics need no
join. All are derived: the summarizer rewrites them with the run (INV-2), and this migration backfills existing rows
from `summary` / `runs`. On a very large deployment run the backfill off-peak (it rewrites every span row once).

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

MONEY = ("cost_usd", "retry_cost_usd")
COUNTS = (
    "llm_calls",
    "tool_calls",
    "retry_count",
    "retries_unattributed",
    "files_modified",
    "unpriced_calls",
    "tool_spans_finished",
    "tool_spans_ok",
    "llm_spans_finished",
    "llm_spans_ok",
)
# typed column -> key in runs.summary
SOURCE_KEY = {"cost_usd": "estimated_cost_usd", "retry_cost_usd": "retry_cost_usd"}


def upgrade() -> None:
    for name in MONEY:
        op.add_column(
            "runs", sa.Column(name, sa.Numeric(20, 9), server_default="0", nullable=False)
        )
    for name in COUNTS:
        op.add_column("runs", sa.Column(name, sa.Integer(), server_default="0", nullable=False))
    assignments = ", ".join(
        [f"{c} = coalesce((summary->>'{SOURCE_KEY[c]}')::numeric, 0)" for c in MONEY]
        + [f"{c} = coalesce((summary->>'{c}')::numeric, 0)::int" for c in COUNTS]
    )
    op.execute(f"UPDATE runs SET {assignments} WHERE summary <> '{{}}'::jsonb")

    op.add_column("spans", sa.Column("project_id", sa.UUID(), nullable=True))
    op.add_column("spans", sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE spans s SET project_id = r.project_id, run_started_at = r.started_at "
        "FROM runs r WHERE r.workspace_id = s.workspace_id AND r.id = s.run_id"
    )
    op.create_index("ix_spans_window", "spans", ["workspace_id", "run_started_at", "kind"])
    op.create_index(
        "ix_runs_workspace_started",
        "runs",
        ["workspace_id", sa.text("started_at DESC"), "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_workspace_started", table_name="runs")
    op.drop_index("ix_spans_window", table_name="spans")
    op.drop_column("spans", "run_started_at")
    op.drop_column("spans", "project_id")
    for name in (*COUNTS, *MONEY):
        op.drop_column("runs", name)
