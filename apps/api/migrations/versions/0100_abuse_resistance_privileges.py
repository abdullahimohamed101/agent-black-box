"""Phase 19a review: least privilege for the quota tables, and an index for the throttle's key ids.

* The application only reads, inserts and updates `project_quota_overrides`, `project_usage_daily`
  and `project_artifact_usage` (counters and overrides are upserted; a project is never deleted by
  the application, and the foreign keys' cascades run as the table owner). DELETE on them would let
  a bug or an injection erase quota history, so the runtime role loses it, as 0047 did for the
  audit log.
* `ix_api_keys_active` lets the failed-auth throttle's periodic read of usable key ids (ADR-153)
  scan an index of live keys only instead of the whole table.

Revision ID: 0100
Revises: 0099
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"
NO_DELETE_TABLES = ("project_quota_overrides", "project_usage_daily", "project_artifact_usage")


def upgrade() -> None:
    for table in NO_DELETE_TABLES:
        op.execute(f"REVOKE DELETE ON TABLE {table} FROM {ROLE}")
    op.create_index(
        "ix_api_keys_active",
        "api_keys",
        ["key_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_active", table_name="api_keys")
    for table in NO_DELETE_TABLES:
        op.execute(f"GRANT DELETE ON TABLE {table} TO {ROLE}")
