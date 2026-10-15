"""Identity (Phase 19a step 5): a session device hint (KI-068) and an invitation lookup by email.

* `sessions.device_hint`: one nullable column, a short label such as "Firefox on Linux", derived at
  sign-in from the browser's `User-Agent` against closed vocabularies (never the raw header).
  Sessions that exist before this migration, and sessions minted by the CLI, have none.
* `ix_invitations_open_email`: sign-in now asks "does any workspace hold an open invitation for
  this verified email" before creating a person (KI-076); a partial index keeps that lookup, which
  an anonymous-ish caller can trigger, off a table scan.

Additive and reversible.

Revision ID: 0099
Revises: 0098
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("device_hint", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_sessions_device_hint",
        "sessions",
        "device_hint IS NULL OR char_length(device_hint) <= 64",
    )

    op.create_index(
        "ix_invitations_open_email",
        "invitations",
        ["email"],
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_open_email", table_name="invitations")
    op.drop_constraint("ck_sessions_device_hint", "sessions", type_="check")
    op.drop_column("sessions", "device_hint")
