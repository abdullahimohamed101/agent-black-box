"""Baseline: an intentionally empty revision.

Establishes the Alembic history so CI can assert the up/down/up cycle from day one.
Domain tables arrive in Phase 2 as additive revisions; never edit this file afterwards.

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
