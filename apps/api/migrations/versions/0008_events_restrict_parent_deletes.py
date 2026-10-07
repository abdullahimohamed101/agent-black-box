"""Close the cascade path to events (KI-020, INV-1).

`events` referenced `runs` with ON DELETE CASCADE, so anyone allowed to delete a run (or, through
the other foreign keys, a project or workspace) removed its events with the table owner's rights,
whatever the runtime role's privileges on `events` were. Now the foreign key is RESTRICT and the
runtime role cannot delete the parents of events at all: the application never deletes tenants,
projects, agents or runs. Retention (Phase 19) must be a privileged job run as the owner.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

NAME = "events_workspace_id_run_id_fkey"
PARENTS = ("runs", "agents", "projects", "workspaces")


def _fk(on_delete: str) -> None:
    op.execute(f"ALTER TABLE events DROP CONSTRAINT {NAME}")
    op.execute(
        f"ALTER TABLE events ADD CONSTRAINT {NAME} FOREIGN KEY (workspace_id, run_id) "
        f"REFERENCES runs (workspace_id, id) ON DELETE {on_delete}"
    )


def upgrade() -> None:
    _fk("RESTRICT")
    for table in PARENTS:
        op.execute(f"REVOKE DELETE ON TABLE {table} FROM abb_runtime")


def downgrade() -> None:
    for table in PARENTS:
        op.execute(f"GRANT DELETE ON TABLE {table} TO abb_runtime")
    _fk("CASCADE")
