"""Server-only secrets (Phase 14 review fix P1-1, ADR-141 amendment 2).

One append-only table of install-level secrets. The first user is the key that derives the ids of
server-written `policy.*` and `approval.*` events: an agent that could compute those ids could
ingest a harmless event under one first and make the server's insert a silent conflict, erasing
the record of a decision. The secret is random, generated once per installation (by the API on
first use, under an advisory lock) and shared by every API process and worker through the
database; nothing in code or configuration holds it.

The application role may SELECT and INSERT but never UPDATE, DELETE or TRUNCATE. The current
secret of a name is its newest row, so rotation is an INSERT by the owner role (`INSERT INTO
server_secrets (name, secret) VALUES ('event-id', gen_random_bytes(32))`) followed by a restart
of the API and worker processes (they cache the secret). Rotation changes derived ids of future
events only; stored events are never rewritten.

`APPEND_ONLY_TABLES` is read by `tests/test_runtime_role.py`.

Revision ID: 0094
Revises: 0093
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"
APPEND_ONLY_TABLES = ("server_secrets",)


def upgrade() -> None:
    op.create_table(
        "server_secrets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("octet_length(secret) >= 32", name="ck_server_secrets_length"),
        sa.CheckConstraint("name ~ '^[a-z][a-z0-9-]{0,63}$'", name="ck_server_secrets_name"),
    )
    op.create_index("ix_server_secrets_name", "server_secrets", ["name", "id"])
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE server_secrets FROM {ROLE}")


def downgrade() -> None:
    op.drop_table("server_secrets")
