"""Widen money columns to numeric(38,9): hostile cost values must not overflow a column.

A per-call cost is clamped to 1,000,000 USD by the cost engine (ADR-040), but sums over many calls and runs in one
rollup row, and operator-set override prices, can exceed numeric(20,9) (about 1e11). Widening the precision of a
numeric column changes only its type modifier: PostgreSQL does not rewrite the table and the lock is brief.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-08
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

MONEY_COLUMNS = {
    "runs": ("cost_usd", "retry_cost_usd"),
    "cost_calculations": (
        "input_cost",
        "output_cost",
        "cached_cost",
        "request_cost",
        "estimated_total",
        "reported_total",
        "client_total",
        "total",
    ),
    "pricing_overrides": (
        "input_per_million",
        "output_per_million",
        "cached_input_per_million",
        "request_price",
    ),
    "analytics_runs_daily": ("cost_usd", "retry_cost_usd"),
    "analytics_cost_daily": ("total_usd",),
    "analytics_top_runs": ("cost_usd", "retry_cost_usd"),
}


def _retype(precision: int) -> None:
    for table, columns in MONEY_COLUMNS.items():
        for column in columns:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE numeric({precision}, 9)")


def upgrade() -> None:
    _retype(38)


def downgrade() -> None:
    _retype(20)
