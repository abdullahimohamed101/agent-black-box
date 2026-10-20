"""Project and workspace purge (Phase 19b step 5, ADR-163): the `purging` state, the purge record,
and the retention role's extra privileges.

* `projects.purge_state` and `workspaces.purge_state` (`active` | `purging`, CHECK, partial index):
  the owner-run purge command sets `purging` first; from then on the project's keys are refused,
  its project (or the whole workspace) reads as "not found" and ingestion stops. The application
  only reads the column.
* `purge_tombstones`: a platform-level record, one row per purge request, with NO foreign key (it
  must outlive the workspace it describes) and no names or content: ids, a hash of the typed
  slug, counts per table, the steps done. The same row is the resume state of a running purge.
  Only the retention role writes it; the application role may only read it.
* Grants of `abb_retention`, all explicit (its earlier grants stay): `UPDATE (purge_state)` on
  projects and workspaces, `DELETE` on the tables that hold a project's or workspace's rows, and
  for the tables that hold credentials or content ONLY column-level `SELECT` of the key columns
  the `WHERE` clauses need. `SELECT *` on `api_keys`, `alert_destinations`, `policies` and
  `approvals` stays impossible.

Revision ID: 0104
Revises: 0103
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

ROLE = "abb_retention"
RUNTIME = "abb_runtime"

# Tables whose rows the purge deletes directly: role gets DELETE.
DELETE_TABLES = (
    "projects", "workspaces", "agents", "agent_versions", "api_keys", "approvals", "evaluators",
    "experiments", "policies", "policy_versions", "pricing_overrides", "alert_rules",
    "alert_destinations", "alert_notifications", "invitations", "workspace_members",
)  # fmt: skip
# Column-level SELECT only (credential and content tables): exactly the key columns.
COLUMN_SELECT = {
    "agents": ("workspace_id", "project_id", "id"),
    "agent_versions": ("workspace_id", "agent_id", "id"),
    "api_keys": ("workspace_id", "project_id", "id"),
    "approvals": ("workspace_id", "project_id", "id"),
    "evaluators": ("workspace_id", "project_id", "id"),
    "experiments": ("workspace_id", "project_id", "id"),
    "experiment_variants": ("workspace_id", "experiment_id"),
    "experiment_runs": ("workspace_id", "experiment_id"),
    "policies": ("workspace_id", "project_id", "id"),
    "policy_versions": ("workspace_id", "project_id", "id"),
    "pricing_overrides": ("workspace_id", "project_id", "id"),
    "alert_rules": ("workspace_id", "project_id", "id"),
    "alert_rule_destinations": ("workspace_id", "rule_id"),
    "alert_destinations": ("workspace_id", "id"),
    "alert_notifications": ("workspace_id", "project_id", "id"),
    "project_quota_overrides": ("workspace_id", "project_id"),
    "project_security_settings": ("workspace_id", "project_id"),
}
FAILURES = "('artifact_store', 'database', 'timeout', 'internal')"


def upgrade() -> None:
    for table in ("projects", "workspaces"):
        op.add_column(
            table, sa.Column("purge_state", sa.Text(), server_default="active", nullable=False)
        )
        op.create_check_constraint(
            f"ck_{table}_purge_state", table, "purge_state IN ('active', 'purging')"
        )
    op.create_index(
        "ix_projects_purging",
        "projects",
        ["workspace_id", "id"],
        postgresql_where=sa.text("purge_state = 'purging'"),
    )
    op.create_index(
        "ix_workspaces_purging",
        "workspaces",
        ["id"],
        postgresql_where=sa.text("purge_state = 'purging'"),
    )

    op.create_table(
        "purge_tombstones",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        # No foreign keys: this record outlives the project or workspace it describes.
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID()),
        sa.Column("scope_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="running", nullable=False),
        sa.Column("tool_version", sa.Text(), nullable=False),
        sa.Column("steps_done", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("counts", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "user_candidates", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_code", sa.Text()),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("scope IN ('project', 'workspace')", name="ck_purge_tombstones_scope"),
        sa.CheckConstraint(
            "(scope = 'project') = (project_id IS NOT NULL)", name="ck_purge_tombstones_project"
        ),
        sa.CheckConstraint("state IN ('running', 'completed')", name="ck_purge_tombstones_state"),
        sa.CheckConstraint(
            "(state = 'completed') = (completed_at IS NOT NULL)", name="ck_purge_tombstones_done"
        ),
        sa.CheckConstraint(
            f"failure_code IS NULL OR failure_code IN {FAILURES}",
            name="ck_purge_tombstones_failure",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_purge_tombstones_attempts"),
    )
    # At most one running purge per scope, whatever happens to the advisory lock.
    op.create_index(
        "uq_purge_running_project",
        "purge_tombstones",
        ["workspace_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("state = 'running' AND scope = 'project'"),
    )
    op.create_index(
        "uq_purge_running_workspace",
        "purge_tombstones",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("state = 'running' AND scope = 'workspace'"),
    )
    op.create_index("ix_purge_tombstones_workspace", "purge_tombstones", ["workspace_id"])

    # The application may read the record (ids and counts only, like the retention reports) and
    # can neither forge nor erase it (default privileges gave it everything).
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE purge_tombstones FROM {RUNTIME}")

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON purge_tombstones TO {ROLE}")
    op.execute(f"GRANT UPDATE (purge_state) ON projects TO {ROLE}")
    op.execute(f"GRANT UPDATE (purge_state) ON workspaces TO {ROLE}")
    op.execute(f"GRANT DELETE ON {', '.join(DELETE_TABLES)} TO {ROLE}")
    for table, columns in COLUMN_SELECT.items():
        op.execute(f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {ROLE}")


def downgrade() -> None:
    for table, columns in COLUMN_SELECT.items():
        op.execute(f"REVOKE SELECT ({', '.join(columns)}) ON {table} FROM {ROLE}")  # noqa: S608
    op.execute(f"REVOKE DELETE ON {', '.join(DELETE_TABLES)} FROM {ROLE}")
    op.execute(f"REVOKE UPDATE (purge_state) ON workspaces FROM {ROLE}")
    op.execute(f"REVOKE UPDATE (purge_state) ON projects FROM {ROLE}")
    op.execute(f"REVOKE ALL ON TABLE purge_tombstones FROM {ROLE}")
    op.drop_index("ix_purge_tombstones_workspace", table_name="purge_tombstones")
    op.drop_index("uq_purge_running_workspace", table_name="purge_tombstones")
    op.drop_index("uq_purge_running_project", table_name="purge_tombstones")
    op.drop_table("purge_tombstones")
    op.drop_index("ix_workspaces_purging", table_name="workspaces")
    op.drop_index("ix_projects_purging", table_name="projects")
    for table in ("workspaces", "projects"):
        op.drop_constraint(f"ck_{table}_purge_state", table, type_="check")
        op.drop_column(table, "purge_state")
