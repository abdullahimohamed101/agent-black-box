"""Index outbox jobs by (workspace, type, dedupe key, status).

Run listings and run detail look up each run's summarization state by dedupe key. Finished jobs are
kept, so without this index every listing scans the whole job history (measured: a 300k-row table
needs a full parallel sequential scan per request).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_dedupe",
        "outbox_jobs",
        ["workspace_id", "job_type", "dedupe_key", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_dedupe", table_name="outbox_jobs")
