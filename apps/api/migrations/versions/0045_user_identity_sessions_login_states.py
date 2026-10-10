"""Identity for people: OIDC identity columns on users, server-side sessions and login state (ADR-060).

`sessions` and `login_states` are user-level, deliberately not tenant-keyed: a session belongs to a
person, who may be a member of several workspaces, and a login attempt exists before anyone is known.
They are looked up by the SHA-256 of an unguessable token, like `api_keys.key_id` is by its public id.
Additive and backfill-free: existing users (there are none in practice) keep NULL identity columns,
which means "pre-provisioned, not yet linked".

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("provider", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("provider_subject", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    # NULLs are distinct in a unique constraint, so any number of unlinked users may coexist.
    op.create_unique_constraint("uq_users_identity", "users", ["provider", "provider_subject"])
    op.create_check_constraint(
        "ck_users_identity_pair", "users", "(provider IS NULL) = (provider_subject IS NULL)"
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires", "sessions", ["expires_at"])

    op.create_table(
        "login_states",
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
        sa.CheckConstraint("octet_length(state_hash) = 32", name="ck_login_states_state_hash"),
    )
    op.create_index("ix_login_states_expires", "login_states", ["expires_at"])


def downgrade() -> None:
    op.drop_table("login_states")
    op.drop_table("sessions")
    op.drop_constraint("ck_users_identity_pair", "users", type_="check")
    op.drop_constraint("uq_users_identity", "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "provider_subject")
    op.drop_column("users", "provider")
