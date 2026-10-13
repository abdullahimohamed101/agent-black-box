"""Per-project security settings (Phase 13, ADR-130): capture mode, detector mutes, allowlist.

Additive. One optional row per project; a project without a row behaves as METADATA_ONLY with no
mutes and no allowlist, so no backfill is needed and existing projects get the safe default. That
is a behaviour change worth knowing: a project whose SDK sends payloads (FULL) stops storing them
until its capture mode is set to FULL (`python -m abb_api.cli set-capture-mode`). The runtime role
keeps full DML through the default privileges of 0007 (configuration, not append-only history).

Revision ID: 0090
Revises: 0071
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0090"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_security_settings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("capture_mode", sa.Text(), server_default="METADATA_ONLY", nullable=False),
        sa.Column(
            "muted_detectors",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "destination_allowlist",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "project_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "capture_mode IN ('FULL', 'METADATA_ONLY', 'DISABLED')", name="ck_security_capture_mode"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(muted_detectors) = 'array' AND jsonb_array_length(muted_detectors) <= 64",
            name="ck_security_muted_detectors",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(destination_allowlist) = 'array' "
            "AND jsonb_array_length(destination_allowlist) <= 256",
            name="ck_security_destination_allowlist",
        ),
        sa.CheckConstraint("char_length(updated_by) <= 128", name="ck_security_updated_by"),
    )


def downgrade() -> None:
    op.drop_table("project_security_settings")
