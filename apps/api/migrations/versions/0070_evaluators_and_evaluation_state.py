"""Evaluators and the state of an evaluation (Phase 11, ADR-090).

Additive: `evaluators` is new; `evaluations` (a skeleton since 0005, never written) gains the
columns of a stateful result. Every new NOT NULL column has a server default, so no backfill is
needed. A trigger makes a completed or failed evaluation immutable (INV-1): the handler's
`UPDATE ... WHERE state = 'pending'` is the only way out of pending, and the database refuses
anything else. The application role keeps full DML on both tables (default privileges, 0008).
The CHECKs are backstops for byte limits the pydantic models enforce first.

Revision ID: 0070
Revises: 0060
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070"
down_revision = "0080"
branch_labels = None
depends_on = None

KINDS = "'deterministic', 'metric', 'human', 'llm_judge'"
STATES = "'pending', 'running', 'completed', 'failed'"


def upgrade() -> None:
    op.create_table(
        "evaluators",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("implementation", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("workspace_id", "name", name="uq_evaluators_name"),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_evaluators_kind"),
        sa.CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_evaluators_config_object"),
        sa.CheckConstraint("octet_length(config::text) <= 16384", name="ck_evaluators_config_size"),
        sa.CheckConstraint(
            "char_length(name) <= 64 AND char_length(implementation) <= 64 "
            "AND char_length(description) <= 500",
            name="ck_evaluators_text_sizes",
        ),
    )

    json_object = sa.text("'{}'::jsonb")
    op.add_column("evaluations", sa.Column("evaluator_id", sa.UUID(), nullable=True))
    op.add_column(
        "evaluations",
        sa.Column("kind", sa.Text(), server_default="deterministic", nullable=False),
    )
    op.add_column("evaluations", sa.Column("implementation", sa.Text(), nullable=True))
    op.add_column(
        "evaluations", sa.Column("state", sa.Text(), server_default="pending", nullable=False)
    )
    op.add_column("evaluations", sa.Column("explanation", sa.Text(), nullable=True))
    op.add_column(
        "evaluations",
        sa.Column("metrics", postgresql.JSONB(), server_default=json_object, nullable=False),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "evidence_event_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "config_snapshot", postgresql.JSONB(), server_default=json_object, nullable=False
        ),
    )
    op.add_column("evaluations", sa.Column("error_code", sa.Text(), nullable=True))
    op.add_column("evaluations", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("evaluations", sa.Column("requested_by", sa.UUID(), nullable=True))
    op.add_column("evaluations", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "evaluations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_evaluations_requested_by", "evaluations", "users", ["requested_by"], ["id"]
    )
    op.create_foreign_key(
        "fk_evaluations_evaluator",
        "evaluations",
        "evaluators",
        ["workspace_id", "evaluator_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("ck_evaluations_kind", "evaluations", f"kind IN ({KINDS})")
    op.create_check_constraint("ck_evaluations_state", "evaluations", f"state IN ({STATES})")
    op.create_check_constraint("ck_evaluations_score", "evaluations", "score BETWEEN 0 AND 1")
    op.create_check_constraint(
        "ck_evaluations_explanation_size", "evaluations", "octet_length(explanation) <= 16384"
    )
    op.create_check_constraint(
        "ck_evaluations_metrics_size", "evaluations", "octet_length(metrics::text) <= 16384"
    )
    op.create_check_constraint(
        "ck_evaluations_metadata_size", "evaluations", "octet_length(metadata::text) <= 16384"
    )
    op.create_check_constraint(
        "ck_evaluations_config_size", "evaluations", "octet_length(config_snapshot::text) <= 16384"
    )
    op.create_check_constraint(
        "ck_evaluations_evidence",
        "evaluations",
        "jsonb_typeof(evidence_event_ids) = 'array' "
        "AND octet_length(evidence_event_ids::text) <= 8192",
    )
    op.create_check_constraint(
        "ck_evaluations_text_sizes",
        "evaluations",
        "char_length(label) <= 64 AND char_length(error_code) <= 64 AND char_length(error) <= 512 "
        "AND char_length(evaluator_name) <= 64 AND char_length(evaluator_version) <= 32",
    )
    op.create_index(
        "ix_evaluations_run_evaluator",
        "evaluations",
        ["workspace_id", "run_id", "evaluator_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_evaluations_open",
        "evaluations",
        ["workspace_id", "state"],
        postgresql_where=sa.text("state IN ('pending', 'running')"),
    )
    op.execute(
        """
        CREATE FUNCTION evaluations_refuse_terminal_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'a completed or failed evaluation is immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER evaluations_terminal_immutable BEFORE UPDATE ON evaluations "
        "FOR EACH ROW WHEN (OLD.state IN ('completed', 'failed')) "
        "EXECUTE FUNCTION evaluations_refuse_terminal_update()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER evaluations_terminal_immutable ON evaluations")
    op.execute("DROP FUNCTION evaluations_refuse_terminal_update()")
    op.drop_index("ix_evaluations_open", table_name="evaluations")
    op.drop_index("ix_evaluations_run_evaluator", table_name="evaluations")
    for name in (
        "ck_evaluations_text_sizes",
        "ck_evaluations_evidence",
        "ck_evaluations_config_size",
        "ck_evaluations_metadata_size",
        "ck_evaluations_metrics_size",
        "ck_evaluations_explanation_size",
        "ck_evaluations_score",
        "ck_evaluations_state",
        "ck_evaluations_kind",
    ):
        op.drop_constraint(name, "evaluations", type_="check")
    op.drop_constraint("fk_evaluations_evaluator", "evaluations", type_="foreignkey")
    op.drop_constraint("fk_evaluations_requested_by", "evaluations", type_="foreignkey")
    for column in (
        "completed_at",
        "started_at",
        "requested_by",
        "error",
        "error_code",
        "config_snapshot",
        "evidence_event_ids",
        "metrics",
        "explanation",
        "state",
        "implementation",
        "kind",
        "evaluator_id",
    ):
        op.drop_column("evaluations", column)
    op.drop_table("evaluators")
