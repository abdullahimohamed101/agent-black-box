"""Artifacts become real: project scope, name, media type; no foreign key to runs (ADR-030).

The skeleton table from 0005 was never written by any code. An artifact can arrive before the first
event of its run (arrival order is never trusted), so the run foreign key goes; tenant integrity is
kept by `(workspace_id, ...)` keys and the project foreign key added here.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM artifacts)")).scalar():
        raise RuntimeError("artifacts is not empty; the skeleton table was never written to")
    op.drop_constraint("artifacts_workspace_id_run_id_fkey", "artifacts", type_="foreignkey")
    op.add_column("artifacts", sa.Column("project_id", sa.UUID(), nullable=False))
    op.add_column("artifacts", sa.Column("name", sa.Text(), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column("media_type", sa.Text(), server_default="text/plain", nullable=False),
    )
    op.create_foreign_key(
        "artifacts_workspace_id_project_id_fkey",
        "artifacts",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_index("ix_artifacts_run", "artifacts", ["workspace_id", "run_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_run", table_name="artifacts")
    op.drop_constraint("artifacts_workspace_id_project_id_fkey", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "media_type")
    op.drop_column("artifacts", "name")
    op.drop_column("artifacts", "project_id")
    # Rows whose run never arrived cannot satisfy the restored foreign key.
    op.execute(
        "DELETE FROM artifacts a WHERE NOT EXISTS "
        "(SELECT 1 FROM runs r WHERE r.workspace_id = a.workspace_id AND r.id = a.run_id)"
    )
    op.create_foreign_key(
        "artifacts_workspace_id_run_id_fkey",
        "artifacts",
        "runs",
        ["workspace_id", "run_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
