"""Append-only audit log (ADR-062, D11, INV-1 for administrative history).

The application role may SELECT and INSERT, never UPDATE, DELETE or TRUNCATE. Default privileges
grant full DML on a table at creation, so the REVOKE runs after CREATE TABLE in this migration. The
downgrade drops the table only (the role and its default privileges belong to 0007).
`details` is a small JSON object (CHECKs below); the application also refuses secret-looking keys.

`APPEND_ONLY_TABLES` is imported by `tests/test_runtime_role.py`, so a future append-only table has
to be declared here (or in its own migration's constant) and cannot join the exception silently.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"
APPEND_ONLY_TABLES = ("events", "audit_log")


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.CheckConstraint("actor_kind IN ('user', 'api_key', 'cli')", name="ck_audit_actor_kind"),
        sa.CheckConstraint("outcome IN ('allowed', 'denied')", name="ck_audit_outcome"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_audit_details_object"),
        sa.CheckConstraint("pg_column_size(details) < 8192", name="ck_audit_details_size"),
        sa.CheckConstraint(
            "char_length(actor_id) <= 128 AND char_length(action) <= 64 "
            "AND char_length(resource_kind) <= 64 AND char_length(resource_id) <= 128 "
            "AND char_length(request_id) <= 64",
            name="ck_audit_text_sizes",
        ),
    )
    # Plain columns: Postgres scans the index backwards for newest-first paging.
    op.create_index("ix_audit_workspace_time", "audit_log", ["workspace_id", "occurred_at", "id"])
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM {ROLE}")


def downgrade() -> None:
    op.drop_table("audit_log")
