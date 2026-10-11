"""Derived per-run search document (ADR-100, ADR-101): `run_search`.

One row per summarised run, written by the summarizer in the same transaction as the run row and
rebuildable from events (INV-2). Metadata and content live in separate columns with separate
truncation flags: an actor without `payload.read` is never given a statement that touches the
content columns (ADR-101). Arrays hold exact values for equality, prefix and substring matching;
the two `tsvector` columns hold literals built in Python (never `to_tsvector`).

The table is created empty: no backfill here. Existing runs are indexed by re-summarising them
(`python -m abb_api.cli search-rebuild`). The foreign key to `runs` takes a brief SHARE ROW
EXCLUSIVE lock on `runs` that waits behind long transactions: run this outside a bulk rebuild.
The runtime role gets DML through the default privileges of 0007 (derived state, not append-only).

Revision ID: 0080
Revises: 0047
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0080"
down_revision = "0047"
branch_labels = None
depends_on = None

_EMPTY_ARRAY = sa.text("'{}'::text[]")
_ARRAYS = ("event_types", "tools", "models", "providers", "agents", "tags")
_CONTENT_ARRAYS = ("file_paths", "commands")


def upgrade() -> None:
    op.create_table(
        "run_search",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary_version", sa.Integer(), nullable=False),
        sa.Column("document_hash", sa.LargeBinary(), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("security_events", sa.Integer(), server_default="0", nullable=False),
        *(
            sa.Column(
                name, postgresql.ARRAY(sa.Text()), server_default=_EMPTY_ARRAY, nullable=False
            )
            for name in _ARRAYS
        ),
        sa.Column(
            "metadata_doc",
            postgresql.TSVECTOR(),
            server_default=sa.text("''::tsvector"),
            nullable=False,
        ),
        sa.Column("metadata_truncated", sa.Boolean(), server_default=sa.false(), nullable=False),
        *(
            sa.Column(
                name, postgresql.ARRAY(sa.Text()), server_default=_EMPTY_ARRAY, nullable=False
            )
            for name in _CONTENT_ARRAYS
        ),
        sa.Column(
            "content_doc",
            postgresql.TSVECTOR(),
            server_default=sa.text("''::tsvector"),
            nullable=False,
        ),
        sa.Column("content_truncated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "run_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_run_search_project_started",
        "run_search",
        ["workspace_id", "project_id", sa.text("started_at DESC"), "run_id"],
    )
    op.create_index("ix_run_search_meta", "run_search", ["metadata_doc"], postgresql_using="gin")
    op.create_index("ix_run_search_content", "run_search", ["content_doc"], postgresql_using="gin")
    op.create_index(
        "ix_run_search_event_types", "run_search", ["event_types"], postgresql_using="gin"
    )
    op.create_index("ix_run_search_tools", "run_search", ["tools"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("run_search")
