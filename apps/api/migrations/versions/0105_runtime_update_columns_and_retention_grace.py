"""Review fixes for Phase 19b: the runtime role's UPDATE narrowed to columns, and the retention
grace period (ADR-159 amendment, ADR-160 amendment).

* P2-4. `abb_runtime` held table-level UPDATE on `runs`, `projects` and `workspaces` (0007's grant
  on every table), so a compromised API process could flip `runs.trace_state` (hide a trace, or
  stop the retention job from selecting a run) or `purge_state` (un-hide a purging project). The
  table-level privilege is replaced by column-level UPDATE on exactly the columns the application
  writes: the summarizer's list on `runs` (`RunRepository.apply_derivation`), and `name` alone on
  `projects` and `workspaces` (nothing writes it; PostgreSQL requires UPDATE on at least one column
  for the `FOR NO KEY UPDATE` / `FOR KEY SHARE` row locks the application takes there). The
  retention role's own column grants (`trace_state`, `purge_state`) are untouched.
* P2-3. `retention_policies` keeps, per tier, the value that was in force before a shortening
  (`*_prev_days` / `*_prev_forever`, NULL/false = the environment default) and when the shortening
  takes effect (`*_effective_at`). Until then the retention job enforces the LONGER of the two.
  Lengthening, and a shortening with no grace period, leave `*_effective_at` NULL.

Revision ID: 0105
Revises: 0104
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None

RUNTIME = "abb_runtime"
TIERS = ("artifacts", "trace", "run", "audit")
FLOOR_DAYS = 7
CEILING_DAYS = 36500

# Every column `RunRepository.apply_derivation` writes (status ... updated_at, the typed figures).
RUN_COLUMNS = (
    "status", "ordering_mode", "started_at", "completed_at", "duration_ms", "trace_id",
    "agent_slug", "name", "summary", "summary_version", "updated_at", "cost_usd",
    "retry_cost_usd", "llm_calls", "tool_calls", "retry_count", "retries_unattributed",
    "files_modified", "unpriced_calls", "tool_spans_finished", "tool_spans_ok",
    "llm_spans_finished", "llm_spans_ok",
)  # fmt: skip
LOCK_ONLY = ("projects", "workspaces")


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE ON TABLE runs, projects, workspaces FROM {RUNTIME}")
    op.execute(f"GRANT UPDATE ({', '.join(RUN_COLUMNS)}) ON runs TO {RUNTIME}")
    for table in LOCK_ONLY:
        op.execute(f"GRANT UPDATE (name) ON {table} TO {RUNTIME}")

    for tier in TIERS:
        op.add_column("retention_policies", sa.Column(f"{tier}_prev_days", sa.Integer()))
        op.add_column(
            "retention_policies",
            sa.Column(
                f"{tier}_prev_forever",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )
        op.add_column(
            "retention_policies", sa.Column(f"{tier}_effective_at", sa.DateTime(timezone=True))
        )
        op.create_check_constraint(
            f"ck_retention_policies_{tier}_prev_days",
            "retention_policies",
            f"{tier}_prev_days IS NULL OR {tier}_prev_days BETWEEN {FLOOR_DAYS} AND {CEILING_DAYS}",
        )
        op.create_check_constraint(
            f"ck_retention_policies_{tier}_prev_forever",
            "retention_policies",
            f"NOT {tier}_prev_forever OR {tier}_prev_days IS NULL",
        )


def downgrade() -> None:
    for tier in TIERS:
        op.drop_constraint(f"ck_retention_policies_{tier}_prev_forever", "retention_policies")
        op.drop_constraint(f"ck_retention_policies_{tier}_prev_days", "retention_policies")
        op.drop_column("retention_policies", f"{tier}_effective_at")
        op.drop_column("retention_policies", f"{tier}_prev_forever")
        op.drop_column("retention_policies", f"{tier}_prev_days")

    for table in LOCK_ONLY:
        op.execute(f"REVOKE UPDATE (name) ON {table} FROM {RUNTIME}")
    op.execute(f"REVOKE UPDATE ({', '.join(RUN_COLUMNS)}) ON runs FROM {RUNTIME}")
    op.execute(f"GRANT UPDATE ON TABLE runs, projects, workspaces TO {RUNTIME}")
