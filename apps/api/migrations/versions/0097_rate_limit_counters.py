"""Shared limiter counters (Phase 19a step 1, ADR-152): `rate_limit_counters`.

One row per (scope, workspace, key, fixed window), upserted with `INSERT ... ON CONFLICT DO
UPDATE` so every API process counts into the same place (KI-017, 141, 147, 161). Windows are
aligned to the database clock. `expires_at` is the end of the window plus a grace period; the
counter service deletes expired rows in small bounded batches, served by `ix_rate_limit_counters_expires`,
so the table is bounded by the number of live (scope, key) pairs.

The table holds operational counters, not tenant data and not history: no foreign key (a counter
must never delay deleting a workspace), global scopes use the nil workspace id. The runtime role
gets SELECT/INSERT/UPDATE/DELETE through the default privileges of 0007, which is exactly the DML
the service uses; it needs no DDL.

Revision ID: 0097
Revises: 0096
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope", "workspace_id", "key", "window_start"),
        sa.CheckConstraint("scope ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_rate_limit_counters_scope"),
        sa.CheckConstraint("char_length(key) BETWEEN 1 AND 256", name="ck_rate_limit_counters_key"),
        sa.CheckConstraint("count >= 0", name="ck_rate_limit_counters_count"),
    )
    op.create_index("ix_rate_limit_counters_expires", "rate_limit_counters", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_counters_expires", table_name="rate_limit_counters")
    op.drop_table("rate_limit_counters")
