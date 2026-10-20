"""Data lifecycle foundation (Phase 19b step 1, ADR-159): the retention role, policy and reports.

* `abb_retention` (NOLOGIN until `ABB_RETENTION_DB_PASSWORD` is given at `alembic upgrade`, like
  `abb_runtime`) is the only role that may DELETE the retained tables, including `events` (INV-1 as
  amended by ADR-159). It has no default privileges: every grant below is explicit, and it holds
  nothing on `api_keys`, `server_secrets`, the alert tables, `policies` or `approvals`. Sessions and
  users are readable by column only (never the token hash). The lists are the whole of what the
  job may do; `tests/retention/test_retention_role.py` pins them.
* `retention_policies`: a workspace's per-tier choice (NULL = environment default; `*_forever` is
  explicit). `retention_reports`: counts the tick recorded. `retention_tombstones`: reserved for
  enforcement (step 2). The application reads reports and tombstones but never writes them, so a
  compromised API process cannot forge or erase retention history.

The role is cluster-wide: created if absent, never dropped by a downgrade (another database on the
server may use it); the downgrade only removes this database's grants.

Revision ID: 0101
Revises: 0100
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None

ROLE = "abb_retention"
RUNTIME = "abb_runtime"
TIERS = ("artifacts", "trace", "run", "audit")
FLOOR_DAYS = 7
CEILING_DAYS = 36500

SELECT_TABLES = (
    "workspaces", "projects", "runs", "spans", "events", "artifacts", "findings", "run_findings",
    "run_search", "cost_calculations", "evaluations", "outbox_jobs", "audit_log",
    "rate_limit_counters", "project_usage_daily", "project_artifact_usage", "workspace_members",
    "invitations", "retention_policies", "retention_reports", "retention_tombstones",
)  # fmt: skip
# Column-level SELECT: the job sees ages and ownership, never a credential hash.
SELECT_COLUMNS = {
    "sessions": ("id", "user_id", "created_at", "expires_at", "idle_expires_at", "revoked_at"),
    "users": ("id", "email", "created_at"),
    # Whether a person who never joined a workspace is still referenced (ids only).
    "approvals": ("requested_by_user_id", "decided_by_user_id"),
}
DELETE_TABLES = (
    "events", "spans", "cost_calculations", "findings", "run_findings", "run_search",
    "evaluations", "artifacts", "runs", "outbox_jobs", "audit_log", "rate_limit_counters",
    "project_usage_daily", "sessions", "users", "retention_reports",
)  # fmt: skip
INSERT_TABLES = ("audit_log", "retention_reports", "retention_tombstones")


def _ts(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if now else None,
    )


def upgrade() -> None:
    op.create_table(
        "retention_policies",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *[
            column
            for tier in TIERS
            for column in (
                sa.Column(f"{tier}_days", sa.Integer()),
                sa.Column(
                    f"{tier}_forever", sa.Boolean(), server_default=sa.text("false"), nullable=False
                ),
            )
        ],
        _ts("updated_at", nullable=False, now=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        *[
            constraint
            for tier in TIERS
            for constraint in (
                sa.CheckConstraint(
                    f"{tier}_days IS NULL OR {tier}_days BETWEEN {FLOOR_DAYS} AND {CEILING_DAYS}",
                    name=f"ck_retention_policies_{tier}_days",
                ),
                sa.CheckConstraint(
                    f"NOT {tier}_forever OR {tier}_days IS NULL",
                    name=f"ck_retention_policies_{tier}_forever",
                ),
            )
        ],
        sa.CheckConstraint(
            "char_length(updated_by) <= 128", name="ck_retention_policies_updated_by"
        ),
    )
    op.create_table(
        "retention_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tick_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID()),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("runs", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("events", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("artifact_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("row_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("cutoff_at"),
        _ts("started_at", nullable=False),
        _ts("finished_at", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "tier IN ('artifacts', 'trace', 'run', 'audit', 'operational', 'platform')",
            name="ck_retention_reports_tier",
        ),
        sa.CheckConstraint("mode IN ('dry_run', 'enforce')", name="ck_retention_reports_mode"),
        sa.CheckConstraint(
            "(workspace_id IS NULL) = (tier = 'platform')", name="ck_retention_reports_scope"
        ),
        sa.CheckConstraint(
            "runs >= 0 AND events >= 0 AND artifact_bytes >= 0 AND row_count >= 0",
            name="ck_retention_reports_counts",
        ),
    )
    op.create_index(
        "ix_retention_reports_workspace",
        "retention_reports",
        ["workspace_id", "tier", sa.text("started_at DESC")],
    )
    op.create_table(
        "retention_tombstones",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        _ts("deleted_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "tier"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "tier IN ('artifacts', 'trace', 'run')", name="ck_retention_tombstones_tier"
        ),
        sa.CheckConstraint("mode IN ('dry_run', 'enforce')", name="ck_retention_tombstones_mode"),
    )

    # The application never writes what the job reports, and never deletes a policy.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE retention_reports FROM {RUNTIME}")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE retention_tombstones FROM {RUNTIME}")
    op.execute(f"REVOKE DELETE ON TABLE retention_policies FROM {RUNTIME}")

    op.execute(f"""
        DO $$ BEGIN
            CREATE ROLE {ROLE} NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL;  -- also covers a concurrent migration
        END $$""")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON {', '.join(SELECT_TABLES)} TO {ROLE}")
    for table, columns in SELECT_COLUMNS.items():
        op.execute(f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {ROLE}")
    op.execute(f"GRANT DELETE ON {', '.join(DELETE_TABLES)} TO {ROLE}")
    op.execute(f"GRANT INSERT ON {', '.join(INSERT_TABLES)} TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    for table in SELECT_COLUMNS:
        columns = ", ".join(SELECT_COLUMNS[table])
        op.execute(f"REVOKE SELECT ({columns}) ON {table} FROM {ROLE}")  # noqa: S608
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.drop_table("retention_tombstones")
    op.drop_index("ix_retention_reports_workspace", table_name="retention_reports")
    op.drop_table("retention_reports")
    op.drop_table("retention_policies")
