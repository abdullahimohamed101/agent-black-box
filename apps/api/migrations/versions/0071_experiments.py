"""Experiments, their variants and attached runs (Phase 11, ADR-090).

Additive. A variant belongs to its experiment and an attachment to a run (composite tenant keys,
ON DELETE CASCADE like `evaluations`), so deleting a run or an experiment takes what hangs off it.
Exactly one baseline per experiment is a partial unique index. The application role keeps full DML.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dataset_label", sa.Text(), nullable=True),
        sa.Column(
            "evaluator_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
        sa.CheckConstraint(
            "char_length(name) <= 128 AND char_length(description) <= 2000 "
            "AND char_length(dataset_label) <= 128",
            name="ck_experiments_text_sizes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evaluator_ids) = 'array' AND octet_length(evaluator_ids::text) <= 2048",
            name="ck_experiments_evaluator_ids",
        ),
    )
    op.create_index(
        "ix_experiments_project",
        "experiments",
        ["workspace_id", "project_id", sa.text("created_at DESC")],
    )
    op.create_table(
        "experiment_variants",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("experiment_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "experiment_id", "name", name="uq_experiment_variants_name"
        ),
        sa.CheckConstraint("role IN ('baseline', 'candidate')", name="ck_experiment_variants_role"),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'", name="ck_experiment_variants_config_object"
        ),
        sa.CheckConstraint(
            "octet_length(config::text) <= 8192", name="ck_experiment_variants_config_size"
        ),
        sa.CheckConstraint("char_length(name) <= 64", name="ck_experiment_variants_name_size"),
    )
    op.create_index(
        "uq_experiment_variants_baseline",
        "experiment_variants",
        ["workspace_id", "experiment_id"],
        unique=True,
        postgresql_where=sa.text("role = 'baseline'"),
    )
    op.create_table(
        "experiment_runs",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("experiment_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "experiment_id", "run_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "variant_id"],
            ["experiment_variants.workspace_id", "experiment_variants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_experiment_runs_variant", "experiment_runs", ["workspace_id", "variant_id"])
    op.create_index("ix_experiment_runs_run", "experiment_runs", ["workspace_id", "run_id"])


def downgrade() -> None:
    op.drop_table("experiment_runs")
    op.drop_table("experiment_variants")
    op.drop_table("experiments")
