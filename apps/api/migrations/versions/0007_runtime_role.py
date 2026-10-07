"""Least-privilege runtime role: the application can append events but never rewrite them (KI-020, INV-1).

The migration role (the database owner) keeps DDL. `abb_runtime` is what the API, worker and CLI
connect as: SELECT/INSERT/UPDATE/DELETE on every table except `events`, which is SELECT and INSERT
only. Roles are cluster-wide, so the role is created if absent and never dropped by a downgrade (another
database on the same server may still use it); the downgrade only removes this database's grants.

Tables created by later migrations get full DML through default privileges. A future append-only
table must REVOKE UPDATE/DELETE explicitly in its own migration.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            CREATE ROLE {ROLE} NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL;  -- also covers a concurrent migration
        END $$""")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}")
    op.execute(f"REVOKE ALL ON TABLE alembic_version FROM {ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE events FROM {ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON TABLES TO {ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {ROLE}"
    )


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
