"""Versioned policies (Phase 14, ADR-140): an append-only history of policy documents.

Additive. One row per published version of a policy, per scope: a workspace-wide base
(`project_id IS NULL`) or one project. The current policy of a scope is its highest version, so
there is no mutable "pointer" row to drift. The application role may SELECT and INSERT but never
UPDATE, DELETE or TRUNCATE (like `events` and `audit_log`): a published version, and with it the
policy a past decision cites, cannot be rewritten. Default privileges grant full DML at creation,
so the REVOKE runs after CREATE TABLE. The downgrade drops the table only (the role belongs to
0007). A database without policies behaves as before: no row means the built-in empty policy
(default allow), so no backfill is needed.

`APPEND_ONLY_TABLES` is read by `tests/test_runtime_role.py`, so an append-only table has to be
declared here and cannot join the exception silently.

Revision ID: 0092
Revises: 0091
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"
APPEND_ONLY_TABLES = ("policy_versions",)


def upgrade() -> None:
    op.create_table(
        "policy_versions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document_text", sa.Text(), nullable=False),
        sa.Column("document_sha256", sa.Text(), nullable=False),
        sa.Column("rule_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name="ck_policy_versions_version"),
        sa.CheckConstraint(
            "octet_length(document_text) <= 65536", name="ck_policy_versions_document_size"
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'", name="ck_policy_versions_document_sha256"
        ),
        sa.CheckConstraint("rule_count BETWEEN 0 AND 200", name="ck_policy_versions_rule_count"),
        sa.CheckConstraint("char_length(created_by) <= 128", name="ck_policy_versions_created_by"),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) <= 500", name="ck_policy_versions_comment"
        ),
    )
    # NULL is not equal to NULL in a unique index, so each scope kind gets its own partial one.
    op.create_index(
        "uq_policy_versions_workspace",
        "policy_versions",
        ["workspace_id", "version"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "uq_policy_versions_project",
        "policy_versions",
        ["workspace_id", "project_id", "version"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE policy_versions FROM {ROLE}")


def downgrade() -> None:
    op.drop_table("policy_versions")
