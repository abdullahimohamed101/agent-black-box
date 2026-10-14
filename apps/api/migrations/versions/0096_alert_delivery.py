"""Alert delivery (Phase 17 step 2, ADR-151): two more skip reasons and a delivery-count index.

* `rate_limited` and `daily_cap`: a destination's per-minute rate limit and daily cap close the
  excess notification as `skipped` with a fixed reason, so the history stays honest and the table
  stays bounded.
* `ix_alert_notifications_delivered` serves the rate-limit and daily-cap counts ("how many were
  delivered to this destination in the last minute / day") without scanning the history.

The downgrade keeps every row: it maps a row that uses a new reason to `destination_paused` (the
nearest existing meaning) before restoring the four-value CHECK.

Revision ID: 0096
Revises: 0095
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None

OLD = ("cooldown", "destination_paused", "destination_deleted", "rule_deleted")
NEW = (*OLD, "rate_limited", "daily_cap")


def _check(values: tuple[str, ...]) -> str:
    return "skip_reason IS NULL OR skip_reason IN (" + ", ".join(repr(v) for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_alert_notifications_skip_reason", "alert_notifications", type_="check")
    op.create_check_constraint("ck_alert_notifications_skip_reason", "alert_notifications", _check(NEW))
    op.create_index(
        "ix_alert_notifications_delivered",
        "alert_notifications",
        ["workspace_id", "destination_id", "delivered_at"],
        postgresql_where=sa.text("status = 'delivered'"),
    )


def downgrade() -> None:
    op.drop_index("ix_alert_notifications_delivered", table_name="alert_notifications")
    op.execute(
        "UPDATE alert_notifications SET skip_reason = 'destination_paused' "
        "WHERE skip_reason IN ('rate_limited', 'daily_cap')"
    )
    op.drop_constraint("ck_alert_notifications_skip_reason", "alert_notifications", type_="check")
    op.create_check_constraint("ck_alert_notifications_skip_reason", "alert_notifications", _check(OLD))
