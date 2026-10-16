"""Rollups say how many runs they lack the trace detail of (Phase 19b step 3, ADR-161).

After the retention job removes a run's trace detail, the cost-by-model, span and latency rollups
(built from the deleted tables) no longer include the run while the runs rollup (built from the
run record) still does. `analytics_runs_daily.expired_trace_runs` counts those runs per day so the
reports can say "excludes N runs whose trace has expired". Derived data: a rebuild of a day fills
it; a constant default makes the column a catalog change, not a rewrite. Grants are table-level,
so the retention job's existing rollup grants cover it.

Revision ID: 0103
Revises: 0102
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analytics_runs_daily",
        sa.Column("expired_trace_runs", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("analytics_runs_daily", "expired_trace_runs")
