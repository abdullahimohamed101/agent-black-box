"""Invitations (one-time links bound to an email) and membership bookkeeping (ADR-060, D12).

`invitations` is tenant-keyed `(workspace_id, id)`. Its token is stored only as a SHA-256; the
lookup by that hash happens before the workspace is known (the invitee presents the token while
signed in), the fourth unscoped lookup next to `api_keys.key_id`, `sessions.token_hash` and
`login_states.state_hash`. The partial unique index allows one open invitation per email and
workspace. Additive and backfill-free.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

ROLES = ("OWNER", "ADMIN", "DEVELOPER", "VIEWER", "SECURITY", "BILLING")


def upgrade() -> None:
    op.add_column("workspace_members", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("workspace_members", sa.Column("invited_by", sa.UUID()))
    op.create_foreign_key(
        "fk_workspace_members_invited_by", "workspace_members", "users", ["invited_by"], ["id"]
    )

    op.create_table(
        "invitations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("accepted_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"]),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_invitations_token_hash"),
        sa.CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in ROLES) + ")", name="ck_invitations_role"
        ),
        sa.CheckConstraint(
            "email = lower(email) AND char_length(email) BETWEEN 3 AND 254",
            name="ck_invitations_email",
        ),
    )
    op.create_index(
        "uq_invitations_open_email",
        "invitations",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("invitations")
    op.drop_constraint("fk_workspace_members_invited_by", "workspace_members", type_="foreignkey")
    op.drop_column("workspace_members", "invited_by")
    op.drop_column("workspace_members", "updated_at")
