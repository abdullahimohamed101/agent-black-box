"""Security findings (Phase 13, ADR-131): two more severities and a separate security badge.

Additive. The security detectors report `medium` and `high`, so the severity CHECKs of `findings`
and `run_findings` widen (every existing value stays valid). `run_findings.finding_count` and
`max_severity` keep meaning "reliability findings" so the run list's badge is unchanged;
`security_count` and `security_max_severity` carry the security category beside them. A partial
index serves the workspace Security view. Existing runs have no security findings until they are
re-analysed (`rebuild-findings`; the detector set version also changed, so they read `outdated`).

Revision ID: 0091
Revises: 0090
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None

OLD = "'info', 'warning', 'critical'"
NEW = "'info', 'warning', 'medium', 'high', 'critical'"


def _swap(severities: str) -> None:
    op.drop_constraint("ck_findings_severity", "findings", type_="check")
    op.create_check_constraint("ck_findings_severity", "findings", f"severity IN ({severities})")
    op.drop_constraint("ck_run_findings_severity", "run_findings", type_="check")
    op.create_check_constraint(
        "ck_run_findings_severity",
        "run_findings",
        f"max_severity IS NULL OR max_severity IN ({severities})",
    )


def upgrade() -> None:
    _swap(NEW)
    op.add_column(
        "run_findings",
        sa.Column("security_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("run_findings", sa.Column("security_max_severity", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_run_findings_security_severity",
        "run_findings",
        f"security_max_severity IS NULL OR security_max_severity IN ({NEW})",
    )
    op.create_check_constraint(
        "ck_run_findings_security_count", "run_findings", "security_count >= 0"
    )
    op.create_index(
        "ix_findings_security",
        "findings",
        ["workspace_id", "run_started_at", "id"],
        postgresql_where=sa.text("detector LIKE 'sec.%'"),
    )


def downgrade() -> None:
    op.drop_index("ix_findings_security", table_name="findings")
    op.drop_constraint("ck_run_findings_security_count", "run_findings", type_="check")
    op.drop_constraint("ck_run_findings_security_severity", "run_findings", type_="check")
    op.drop_column("run_findings", "security_max_severity")
    op.drop_column("run_findings", "security_count")
    # Security findings use the new severities; they are derived, so they go with the downgrade.
    op.execute("DELETE FROM findings WHERE detector LIKE 'sec.%'")
    op.execute("DELETE FROM findings WHERE severity IN ('medium', 'high')")
    _swap(OLD)
