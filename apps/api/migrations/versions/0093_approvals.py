"""Approvals (Phase 14 step 2, ADR-141): single-use, intent-bound authorizations, state machine in SQL.

One row per approval request. The bound intent (what was asked for) never changes after the row is
written, and the state machine is enforced by the database as well as by the code, so a bug in a
service or a hand-written statement as the application role cannot rewrite an approval:

* CHECK constraints fix the shape of every state (who decided and when, what is set where).
* A BEFORE UPDATE trigger allows only the real transitions: `REQUESTED` to `APPROVED`, `DENIED`,
  `EXPIRED` or `CANCELLED`; an unconsumed `APPROVED` to a consumed one (`consumed_at` gains a
  value once, never back) or to `EXPIRED`; nothing else, and nothing out of a terminal state.
  Every column of the bound intent is immutable. A request cannot be decided or consumed after
  `expires_at`, nor marked expired before it (all times are supplied by the caller, so the rule
  holds under the tests' injectable clock).
* A BEFORE INSERT trigger lets an approval be born only as `REQUESTED` and unconsumed, so no row
  arrives already decided.
* The application role may SELECT, INSERT and UPDATE (through the trigger) but never DELETE or
  TRUNCATE: an approval, like an audit row, outlives what it authorized.
* A partial unique index allows one open approval per (run, key, intent), which makes the decision
  endpoint idempotent under concurrency.

The trigger is a backstop for bugs and for a stolen application credential; the owner role (which
runs migrations) can still change the schema, as with every table. The audit log's actor kinds
gain `system` for the expiry job. The downgrade maps those rows to `cli` (the actor id still says
`system:...`) and drops the table; the role belongs to 0007.

Revision ID: 0093
Revises: 0092
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None

ROLE = "abb_runtime"
STATES = ("REQUESTED", "APPROVED", "DENIED", "EXPIRED", "CANCELLED")

_GUARD = """
CREATE FUNCTION approvals_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.workspace_id, OLD.id, OLD.project_id, OLD.run_id, OLD.agent_id, OLD.capability,
        OLD.resource_scrubbed, OLD.risk_class, OLD.arguments_scrubbed, OLD.scrub_dropped,
        OLD.scrub_count, OLD.intent_hash, OLD.policy_rule_id, OLD.policy_version,
        OLD.approver_role, OLD.allow_self_approval, OLD.on_expiry, OLD.requested_at,
        OLD.expires_at, OLD.requested_by_key_id, OLD.requested_by_user_id)
       IS DISTINCT FROM
       (NEW.workspace_id, NEW.id, NEW.project_id, NEW.run_id, NEW.agent_id, NEW.capability,
        NEW.resource_scrubbed, NEW.risk_class, NEW.arguments_scrubbed, NEW.scrub_dropped,
        NEW.scrub_count, NEW.intent_hash, NEW.policy_rule_id, NEW.policy_version,
        NEW.approver_role, NEW.allow_self_approval, NEW.on_expiry, NEW.requested_at,
        NEW.expires_at, NEW.requested_by_key_id, NEW.requested_by_user_id) THEN
        RAISE EXCEPTION 'approval: the requested intent is immutable' USING ERRCODE = '23514';
    END IF;

    IF OLD.state = 'REQUESTED' THEN
        IF NEW.state = 'REQUESTED' THEN
            RAISE EXCEPTION 'approval: not a transition' USING ERRCODE = '23514';
        END IF;
        IF NEW.consumed_at IS NOT NULL OR NEW.consumed_run_id IS NOT NULL THEN
            RAISE EXCEPTION 'approval: only an approved request can be consumed'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.state IN ('APPROVED', 'DENIED') AND NEW.decided_at > OLD.expires_at THEN
            RAISE EXCEPTION 'approval: decided after it expired' USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'EXPIRED' AND NEW.closed_at < OLD.expires_at THEN
            RAISE EXCEPTION 'approval: expired before its time' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.state = 'APPROVED' AND OLD.consumed_at IS NULL THEN
        IF NEW.state = 'APPROVED' THEN
            IF NEW.consumed_at IS NULL OR NEW.consumed_run_id IS NULL
               OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
               OR NEW.decided_by_user_id IS DISTINCT FROM OLD.decided_by_user_id
               OR NEW.decision_comment IS DISTINCT FROM OLD.decision_comment
               OR NEW.closed_at IS NOT NULL THEN
                RAISE EXCEPTION 'approval: an approval may only gain consumed_at'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.consumed_at > OLD.expires_at THEN
                RAISE EXCEPTION 'approval: consumed after it expired' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.state = 'EXPIRED' THEN
            IF NEW.decided_at IS DISTINCT FROM OLD.decided_at
               OR NEW.decided_by_user_id IS DISTINCT FROM OLD.decided_by_user_id
               OR NEW.decision_comment IS DISTINCT FROM OLD.decision_comment
               OR NEW.consumed_at IS NOT NULL OR NEW.consumed_run_id IS NOT NULL THEN
                RAISE EXCEPTION 'approval: only the state may change on expiry'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.closed_at < OLD.expires_at THEN
                RAISE EXCEPTION 'approval: expired before its time' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'approval: this state is final' USING ERRCODE = '23514';
END;
$$
"""


_BORN = """
CREATE FUNCTION approvals_born() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state <> 'REQUESTED' OR NEW.consumed_at IS NOT NULL THEN
        RAISE EXCEPTION 'approval: a request starts as REQUESTED' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("resource_scrubbed", sa.Text(), nullable=True),
        sa.Column("risk_class", sa.Text(), nullable=True),
        sa.Column("arguments_scrubbed", postgresql.JSONB(), nullable=True),
        sa.Column("scrub_dropped", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("scrub_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("intent_hash", sa.Text(), nullable=False),
        sa.Column("policy_rule_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("approver_role", sa.Text(), nullable=True),
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False),
        sa.Column("on_expiry", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_key_id", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_run_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.CheckConstraint(
            "state IN (" + ", ".join(repr(s) for s in STATES) + ")", name="ck_approvals_state"
        ),
        sa.CheckConstraint(
            "(state = 'REQUESTED' AND decided_at IS NULL AND decided_by_user_id IS NULL "
            "AND closed_at IS NULL AND consumed_at IS NULL) "
            "OR (state IN ('APPROVED', 'DENIED') AND decided_at IS NOT NULL "
            "AND decided_by_user_id IS NOT NULL AND closed_at IS NULL) "
            "OR (state = 'CANCELLED' AND closed_at IS NOT NULL AND decided_at IS NULL "
            "AND decided_by_user_id IS NULL AND consumed_at IS NULL) "
            "OR (state = 'EXPIRED' AND closed_at IS NOT NULL "
            "AND (decided_at IS NULL) = (decided_by_user_id IS NULL) AND consumed_at IS NULL)",
            name="ck_approvals_shape",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (consumed_run_id IS NULL) "
            "AND (consumed_at IS NULL OR state = 'APPROVED')",
            name="ck_approvals_consumed",
        ),
        sa.CheckConstraint("intent_hash ~ '^[0-9a-f]{64}$'", name="ck_approvals_intent_hash"),
        sa.CheckConstraint("expires_at > requested_at", name="ck_approvals_expiry"),
        sa.CheckConstraint(
            "approver_role IS NULL OR approver_role IN ('OWNER', 'ADMIN', 'DEVELOPER', 'SECURITY')",
            name="ck_approvals_approver_role",
        ),
        sa.CheckConstraint("on_expiry IN ('deny', 'allow')", name="ck_approvals_on_expiry"),
        sa.CheckConstraint(
            "risk_class IS NULL OR risk_class IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="ck_approvals_risk_class",
        ),
        sa.CheckConstraint(
            "char_length(capability) <= 128 AND char_length(agent_id) <= 64 "
            "AND char_length(policy_rule_id) <= 64 AND char_length(requested_by_key_id) <= 128 "
            "AND (resource_scrubbed IS NULL OR char_length(resource_scrubbed) <= 4096) "
            "AND (decision_comment IS NULL OR char_length(decision_comment) <= 1000)",
            name="ck_approvals_text_sizes",
        ),
        sa.CheckConstraint(
            "arguments_scrubbed IS NULL OR (jsonb_typeof(arguments_scrubbed) = 'object' "
            "AND pg_column_size(arguments_scrubbed) < 65536)",
            name="ck_approvals_arguments",
        ),
        sa.CheckConstraint("scrub_count >= 0 AND policy_version >= 0", name="ck_approvals_counts"),
    )
    op.create_index(
        "ix_approvals_pending",
        "approvals",
        ["workspace_id", "requested_at", "id"],
        postgresql_where=sa.text("state = 'REQUESTED'"),
    )
    op.create_index(
        "ix_approvals_listing",
        "approvals",
        ["workspace_id", sa.text("requested_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_approvals_expiry",
        "approvals",
        ["workspace_id", "expires_at"],
        postgresql_where=sa.text(
            "state = 'REQUESTED' OR (state = 'APPROVED' AND consumed_at IS NULL)"
        ),
    )
    op.create_index(
        "uq_approvals_open_intent",
        "approvals",
        ["workspace_id", "run_id", "requested_by_key_id", "intent_hash"],
        unique=True,
        postgresql_where=sa.text(
            "state = 'REQUESTED' OR (state = 'APPROVED' AND consumed_at IS NULL)"
        ),
    )
    op.create_index("ix_approvals_run", "approvals", ["workspace_id", "run_id"])
    op.execute(_GUARD)
    op.execute(
        "CREATE TRIGGER approvals_guard BEFORE UPDATE ON approvals "
        "FOR EACH ROW EXECUTE FUNCTION approvals_guard()"
    )
    op.execute(_BORN)
    op.execute(
        "CREATE TRIGGER approvals_born BEFORE INSERT ON approvals "
        "FOR EACH ROW EXECUTE FUNCTION approvals_born()"
    )
    op.execute(f"REVOKE DELETE, TRUNCATE ON TABLE approvals FROM {ROLE}")

    op.drop_constraint("ck_audit_actor_kind", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_actor_kind", "audit_log", "actor_kind IN ('user', 'api_key', 'cli', 'system')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_actor_kind", "audit_log", type_="check")
    # audit_log is append-only for the application role; the owner role runs this migration.
    op.execute("UPDATE audit_log SET actor_kind = 'cli' WHERE actor_kind = 'system'")
    op.create_check_constraint(
        "ck_audit_actor_kind", "audit_log", "actor_kind IN ('user', 'api_key', 'cli')"
    )
    op.drop_table("approvals")
    op.execute("DROP FUNCTION approvals_guard()")
    op.execute("DROP FUNCTION approvals_born()")
