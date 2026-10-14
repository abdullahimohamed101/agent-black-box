"""Alerting (Phase 17 step 1, ADR-150): destinations, rules, their link table and notifications.

* `alert_destinations` holds where an alert goes. The target (a URL or recipients) and the webhook
  signing secret are stored only as AES-256-GCM ciphertext; `key_id` names the `server_secrets` row
  whose key sealed them (rotation = a newer row; old ciphertexts keep opening with their own).
  `masked_target` is computed once, at save, and is all the API ever shows.
* `alert_rules` says what to watch (one trigger, closed per-trigger filters, a cooldown).
* `alert_rule_destinations` links them many-to-many (composite foreign keys keep both sides in the
  same workspace; deleting either side removes the link).
* `alert_notifications` is one row per (rule, destination, event); its unique key is the dedupe:
  evaluation inserts with ON CONFLICT DO NOTHING, so a redelivered job, a rebuild or a re-summary
  cannot make a second notification. `id` is the stable `alert_id` receivers dedupe on. Deleting a
  rule or a destination keeps the history and clears the reference (`SET NULL (col)` clears only
  that column, never `workspace_id`). The application role keeps full DML on all four tables:
  destinations and rules are hard-deleted by an owner's audited action, and notifications are
  pruned by retention (step 2).

The enumerations are CHECK constraints, so a bug cannot store a trigger, state or status the code
does not know. The downgrade drops the four tables.

Revision ID: 0095
Revises: 0094
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None

TRIGGERS = (
    "approval_requested",
    "approval_expiring",
    "security_finding",
    "run_failed",
    "spend_over",
)
TYPES = ("webhook", "slack", "email")
STATES = ("active", "paused", "failing")
STATUSES = ("pending", "delivered", "failed", "dead", "skipped")
SKIP_REASONS = ("cooldown", "destination_paused", "destination_deleted", "rule_deleted")
SEVERITIES = ("info", "warning", "medium", "high", "critical")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "alert_destinations",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("signing_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("key_id", sa.BigInteger(), nullable=False),
        sa.Column("masked_target", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        _ts("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["key_id"], ["server_secrets.id"]),
        sa.UniqueConstraint("workspace_id", "name", name="uq_alert_destinations_name"),
        sa.CheckConstraint(_in("type", TYPES), name="ck_alert_destinations_type"),
        sa.CheckConstraint(_in("state", STATES), name="ck_alert_destinations_state"),
        sa.CheckConstraint(
            "(type = 'webhook') = (signing_secret_encrypted IS NOT NULL)",
            name="ck_alert_destinations_signing",
        ),
        sa.CheckConstraint(
            "octet_length(config_encrypted) BETWEEN 29 AND 8192",
            name="ck_alert_destinations_config_size",
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_alert_destinations_failures"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 80 AND char_length(masked_target) <= 400 "
            "AND char_length(created_by) <= 128 AND char_length(updated_by) <= 128",
            name="ck_alert_destinations_text_sizes",
        ),
        sa.CheckConstraint(
            "paused_reason IS NULL OR paused_reason IN ('manual', 'failures')",
            name="ck_alert_destinations_paused_reason",
        ),
    )

    op.create_table(
        "alert_rules",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("cooldown_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("last_fired_at"),
        sa.Column("created_by", sa.Text(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        _ts("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_alert_rules_name"),
        sa.CheckConstraint(_in("trigger", TRIGGERS), name="ck_alert_rules_trigger"),
        sa.CheckConstraint(
            "jsonb_typeof(filters) = 'object'", name="ck_alert_rules_filters_object"
        ),
        sa.CheckConstraint("pg_column_size(filters) < 2048", name="ck_alert_rules_filters_size"),
        sa.CheckConstraint("cooldown_seconds BETWEEN 0 AND 604800", name="ck_alert_rules_cooldown"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 80 AND char_length(created_by) <= 128 "
            "AND char_length(updated_by) <= 128",
            name="ck_alert_rules_text_sizes",
        ),
    )
    op.create_index(
        "ix_alert_rules_trigger",
        "alert_rules",
        ["workspace_id", "trigger"],
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "alert_rule_destinations",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "rule_id", "destination_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "rule_id"],
            ["alert_rules.workspace_id", "alert_rules.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "destination_id"],
            ["alert_destinations.workspace_id", "alert_destinations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_alert_rule_destinations_destination",
        "alert_rule_destinations",
        ["workspace_id", "destination_id"],
    )

    op.create_table(
        "alert_notifications",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_slug", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("next_attempt_at", nullable=False, default_now=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("delivered_at"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "rule_id"],
            ["alert_rules.workspace_id", "alert_rules.id"],
            ondelete="SET NULL (rule_id)",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "destination_id"],
            ["alert_destinations.workspace_id", "alert_destinations.id"],
            ondelete="SET NULL (destination_id)",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "rule_id",
            "destination_id",
            "dedupe_key",
            name="uq_alert_notifications_key",
        ),
        sa.CheckConstraint(_in("trigger", TRIGGERS), name="ck_alert_notifications_trigger"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_alert_notifications_status"),
        sa.CheckConstraint(_in("severity", SEVERITIES), name="ck_alert_notifications_severity"),
        sa.CheckConstraint(
            "jsonb_typeof(payload_json) = 'object' AND pg_column_size(payload_json) < 4096",
            name="ck_alert_notifications_payload",
        ),
        sa.CheckConstraint(
            "(status = 'skipped') = (skip_reason IS NOT NULL)", name="ck_alert_notifications_skip"
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR " + _in("skip_reason", SKIP_REASONS),
            name="ck_alert_notifications_skip_reason",
        ),
        sa.CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="ck_alert_notifications_delivered",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_alert_notifications_attempts"),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,47}$'",
            name="ck_alert_notifications_error_code",
        ),
        sa.CheckConstraint(
            "char_length(dedupe_key) <= 200 "
            "AND (agent_slug IS NULL OR char_length(agent_slug) <= 200)",
            name="ck_alert_notifications_text_sizes",
        ),
    )
    op.create_index(
        "ix_alert_notifications_due",
        "alert_notifications",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_alert_notifications_history",
        "alert_notifications",
        ["workspace_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_alert_notifications_cooldown",
        "alert_notifications",
        ["workspace_id", "rule_id", "destination_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("alert_notifications")
    op.drop_table("alert_rule_destinations")
    op.drop_table("alert_rules")
    op.drop_table("alert_destinations")
