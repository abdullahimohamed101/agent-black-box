"""Daily analytics rollups (ADR-043): measured need, rebuildable from the derived tables.

Five tenant-keyed tables keyed by (workspace, project, UTC day, ...). They are rewritten per (workspace, day) by the
`refresh_analytics_day` job and are never the only copy of anything (INV-2). Nothing is backfilled here: a deployment
with existing runs runs `python -m abb_api.cli refresh-analytics --workspace <slug>` once.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

RUN_COUNTS = (
    "runs",
    "retried_runs",
    "runs_with_retry_cost",
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
    "unrebuilt_runs",
)


def _bigint(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.BigInteger(), server_default="0", nullable=False)


def _money(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Numeric(20, 9), server_default="0", nullable=False)


def _text(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Text(), nullable=False)


def _daily(name: str, key: tuple[str, ...], *columns: sa.Column) -> None:  # type: ignore[type-arg]
    op.create_table(
        name,
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        *columns,
        sa.PrimaryKeyConstraint("workspace_id", "project_id", "day", *key),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
    )
    op.create_index(f"ix_{name}_day", name, ["workspace_id", "day"])


def upgrade() -> None:
    _daily(
        "analytics_runs_daily",
        ("agent_slug", "status"),
        _text("agent_slug"),
        _text("status"),
        _bigint("runs"),
        _money("cost_usd"),
        _money("retry_cost_usd"),
        *(_bigint(c) for c in RUN_COUNTS[1:]),
    )
    _daily(
        "analytics_cost_daily",
        ("agent_slug", "provider", "model", "source"),
        _text("agent_slug"),
        _text("provider"),
        _text("model"),
        _text("source"),
        _bigint("calls"),
        _money("total_usd"),
        _bigint("input_tokens"),
        _bigint("output_tokens"),
    )
    _daily(
        "analytics_spans_daily",
        ("kind", "name"),
        _text("kind"),
        _text("name"),
        _bigint("finished"),
        _bigint("ok"),
    )
    _daily(
        "analytics_latency_daily",
        ("series", "name", "bucket"),
        _text("series"),
        _text("name"),
        sa.Column("bucket", sa.SmallInteger(), nullable=False),
        _bigint("n"),
    )
    _daily(
        "analytics_top_runs",
        ("kind", "rank"),
        _text("kind"),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        _money("cost_usd"),
        _money("retry_cost_usd"),
        _bigint("retry_count"),
    )


def downgrade() -> None:
    for name in (
        "analytics_top_runs",
        "analytics_latency_daily",
        "analytics_spans_daily",
        "analytics_cost_daily",
        "analytics_runs_daily",
    ):
        op.drop_table(name)
