"""The runtime role may read the schema revision (Phase 19c step 3, ADR-169).

`/readyz` compares the database's alembic revision with the code's head, so a process that was
deployed before its migration ran reports "not ready" instead of failing requests one by one. The
runtime role could not read `alembic_version` (0007 revoked everything on it, a precaution against
tampering, not secrecy). It gets SELECT only: it still cannot change, insert, delete or truncate
the revision, so it cannot make the migrations believe a step ran. A revision id is not sensitive.

Revision ID: 0106
Revises: 0105
Create Date: 2026-10-16
"""

from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE alembic_version TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE alembic_version FROM {ROLE}")
