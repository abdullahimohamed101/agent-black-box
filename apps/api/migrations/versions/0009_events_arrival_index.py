"""Index events by arrival (workspace, run, received_at, event_id) for live streams.

A live stream follows a run in arrival order: "rows after this position" and "how many rows arrived since T"
(ADR-022). Without this index both read and sort every event of the run on every poll. The existing indexes
cover canonical order (sequence, occurred_at), not `received_at`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_events_run_arrival",
        "events",
        ["workspace_id", "run_id", "received_at", "event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_events_run_arrival", table_name="events")
