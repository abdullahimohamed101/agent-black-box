"""Which tables a project or workspace purge touches, and how (ADR-163, fail-closed).

EVERY table of the schema is classified here, and `tests/retention/test_purge_inventory.py` fails
for one that is not (and for one that names a project or workspace but is classified as kept
without a reason): a table added later cannot silently survive a purge. The deletion steps in
`purge.py` are built from the same lists, so the classification is the behaviour.

* PROJECT   removed by `purge-project` (and so by `purge-workspace`, which purges every project).
* WORKSPACE removed only by `purge-workspace`.
* KEPT      deliberately survives, with the reason.
* EXEMPT    not tenant data at all (nothing to purge), with the reason.

Predicates are SQL over the table, with binds `:w` (workspace id), `:p` (project id) and `:pk`
(the project id as text, for the counters keyed by it). They are constants of this package, never
built from data.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlalchemy import MetaData

from abb_api.retention import inventory as run_inventory


class Kind(StrEnum):
    PROJECT = "purged-with-project"
    WORKSPACE = "purged-with-workspace"
    KEPT = "kept-with-reason"
    EXEMPT = "exempt"


@dataclass(frozen=True)
class Entry:
    kind: Kind
    note: str
    # Rows of one project (PROJECT entries) and of the whole workspace (PROJECT and WORKSPACE).
    project_where: str | None = None
    workspace_where: str | None = None


_PW = "workspace_id = :w AND project_id = :p"
_WW = "workspace_id = :w"
_RUNS_OF_PROJECT = "(SELECT id FROM runs WHERE workspace_id = :w AND project_id = :p)"


def _direct(note: str) -> Entry:
    return Entry(Kind.PROJECT, note, _PW, _WW)


CLASSIFICATION: Final[dict[str, Entry]] = {
    # ---- run data: removed run by run through the retention engine (ADR-160), then swept
    "runs": _direct("each run through the whole-run procedure (files, search, detail, record)"),
    "events": _direct("detail step of each run"),
    "spans": Entry(
        Kind.PROJECT,
        "detail step of each run (selected by run: a span row's project column may be empty)",
        f"workspace_id = :w AND run_id IN {_RUNS_OF_PROJECT}",
        _WW,
    ),
    "cost_calculations": _direct("detail step of each run"),
    "findings": _direct("detail step of each run"),
    "run_findings": _direct("detail step of each run"),
    "run_search": _direct("the search step of each run"),
    "artifacts": _direct("files through the ArtifactStore contract, then rows, then the counter"),
    "evaluations": Entry(
        Kind.PROJECT,
        "detail step of each run",
        f"workspace_id = :w AND run_id IN {_RUNS_OF_PROJECT}",
        _WW,
    ),
    "experiment_runs": Entry(
        Kind.PROJECT,
        "cascades with the run record and with the experiment",
        "workspace_id = :w AND experiment_id IN "
        "(SELECT id FROM experiments WHERE workspace_id = :w AND project_id = :p)",
        _WW,
    ),
    "outbox_jobs": Entry(
        Kind.PROJECT,
        "run-bound jobs go with each run; the rest of the workspace's jobs with the workspace",
        "workspace_id = :w AND job_type = ANY(:job_types) AND dedupe_key IN "
        "(SELECT workspace_id::text || ':' || id::text FROM runs WHERE workspace_id = :w "
        "AND project_id = :p)",
        _WW,
    ),
    "retention_run_progress": _direct("the engine's working state; every flag is checked first"),
    # ---- project configuration and records
    "agents": _direct("project rows"),
    "agent_versions": Entry(
        Kind.PROJECT,
        "versions of the project's agents",
        "workspace_id = :w AND agent_id IN "
        "(SELECT id FROM agents WHERE workspace_id = :w AND project_id = :p)",
        _WW,
    ),
    "api_keys": _direct("the project's keys: they stop working at once ('purging'), then the rows"),
    "approvals": _direct("governance record of the project (includes arguments)"),
    "evaluators": _direct("project rows"),
    "experiments": _direct("project rows; variants and run links cascade"),
    "experiment_variants": Entry(
        Kind.PROJECT,
        "cascades with the experiment",
        "workspace_id = :w AND experiment_id IN "
        "(SELECT id FROM experiments WHERE workspace_id = :w AND project_id = :p)",
        _WW,
    ),
    "policies": _direct("project rows"),
    "policy_versions": _direct("project rows"),
    "pricing_overrides": _direct("project rows"),
    "alert_rules": _direct("the project's rules (KI-170); their destination links cascade"),
    "alert_rule_destinations": Entry(
        Kind.PROJECT,
        "links of the project's rules; the destinations stay until the workspace goes",
        "workspace_id = :w AND rule_id IN "
        "(SELECT id FROM alert_rules WHERE workspace_id = :w AND project_id = :p)",
        _WW,
    ),
    "alert_notifications": _direct("the project's delivery log"),
    "project_security_settings": _direct("cascades with the project row"),
    "project_quota_overrides": _direct("cascades with the project row"),
    "project_usage_daily": _direct("usage counters of the project"),
    "project_artifact_usage": _direct("stored-bytes counter of the project"),
    "analytics_runs_daily": _direct(
        "rollups: rebuilt for the affected days, then the rest deleted"
    ),
    "analytics_cost_daily": _direct("rollups, as above"),
    "analytics_spans_daily": _direct("rollups, as above"),
    "analytics_latency_daily": _direct("rollups, as above"),
    "analytics_top_runs": _direct("rollups, as above"),
    "rate_limit_counters": Entry(
        Kind.PROJECT,
        "the project's ingest counters (keyed by its id); the workspace's other counters go "
        "with the workspace",
        "workspace_id = :w AND key = :pk",
        _WW,
    ),
    "projects": Entry(Kind.PROJECT, "the project row, last", "workspace_id = :w AND id = :p", _WW),
    # ---- workspace level
    "workspaces": Entry(Kind.WORKSPACE, "the workspace row, last", None, "id = :w"),
    "workspace_members": Entry(
        Kind.WORKSPACE, "memberships; the people follow the user rule (ADR-163)", None, _WW
    ),
    "invitations": Entry(Kind.WORKSPACE, "open and used invitations", None, _WW),
    "alert_destinations": Entry(
        Kind.WORKSPACE, "destinations with their sealed configuration and secrets", None, _WW
    ),
    "audit_log": Entry(
        Kind.WORKSPACE,
        "the workspace's own audit log goes with it; purge_tombstones is the record",
        None,
        _WW,
    ),
    "retention_policies": Entry(Kind.WORKSPACE, "cascades with the workspace row", None, _WW),
    "retention_reports": Entry(Kind.WORKSPACE, "cascades with the workspace row", None, _WW),
    "retention_tombstones": Entry(
        Kind.WORKSPACE,
        "ids-only record of removed runs: survives a PROJECT purge (the workspace remains), "
        "cascades with the workspace row",
        None,
        _WW,
    ),
    "analytics_dirty": Entry(Kind.WORKSPACE, "cascades with the workspace row", None, _WW),
    # ---- kept or not tenant data
    "purge_tombstones": Entry(
        Kind.KEPT, "the permanent, ids-only record of every purge; no foreign keys"
    ),
    "users": Entry(
        Kind.KEPT,
        "platform level: removed only by the user rule of a workspace purge (no membership "
        "anywhere, no live session, nothing referencing them)",
    ),
    "sessions": Entry(Kind.KEPT, "platform level: a removed user's expired sessions go with them"),
    "login_states": Entry(Kind.EXEMPT, "platform level sign-in handshakes, no tenant"),
    "server_secrets": Entry(
        Kind.KEPT, "platform level sealing keys: shared by destinations of other workspaces"
    ),
}

# Deleted in this order after the runs and the artifacts of a project are gone. Each entry is
# (table, primary-key columns to batch by | None for ctid, predicate selector).
Drain = tuple[str, tuple[str, ...] | None]

PROJECT_DRAINS: Final[tuple[Drain, ...]] = (
    ("alert_notifications", ("workspace_id", "id")),
    ("alert_rules", ("workspace_id", "id")),
    ("approvals", ("workspace_id", "id")),
    ("evaluators", ("workspace_id", "id")),
    ("experiments", ("workspace_id", "id")),
    ("policies", ("workspace_id", "id")),
    ("policy_versions", ("workspace_id", "id")),
    ("pricing_overrides", ("workspace_id", "id")),
    ("agent_versions", ("workspace_id", "id")),
    ("agents", ("workspace_id", "id")),
    ("api_keys", ("workspace_id", "id")),
    ("analytics_runs_daily", None),
    ("analytics_cost_daily", None),
    ("analytics_spans_daily", None),
    ("analytics_latency_daily", None),
    ("analytics_top_runs", None),
    ("project_usage_daily", None),
    ("rate_limit_counters", None),
    ("retention_run_progress", None),
)
# Cascade with the project row (counted before, never deleted by name).
PROJECT_CASCADED: Final = (
    "project_artifact_usage",
    "project_security_settings",
    "project_quota_overrides",
    "alert_rule_destinations",
    "experiment_variants",
    "experiment_runs",
)

# After every project is gone, whatever of these the workspace still holds (workspace level rows
# and project-less ones), in a foreign-key safe order. The last two steps (audit log, memberships)
# are special-cased by the procedure.
WORKSPACE_DRAINS: Final[tuple[Drain, ...]] = (
    ("alert_notifications", ("workspace_id", "id")),
    ("alert_rules", ("workspace_id", "id")),
    ("alert_destinations", ("workspace_id", "id")),
    ("approvals", ("workspace_id", "id")),
    ("evaluators", ("workspace_id", "id")),
    ("experiments", ("workspace_id", "id")),
    ("policies", ("workspace_id", "id")),
    ("policy_versions", ("workspace_id", "id")),
    ("pricing_overrides", ("workspace_id", "id")),
    ("agent_versions", ("workspace_id", "id")),
    ("agents", ("workspace_id", "id")),
    ("api_keys", ("workspace_id", "id")),
    ("outbox_jobs", None),
    ("rate_limit_counters", None),
    ("analytics_runs_daily", None),
    ("analytics_cost_daily", None),
    ("analytics_spans_daily", None),
    ("analytics_latency_daily", None),
    ("analytics_top_runs", None),
    ("project_usage_daily", None),
    ("retention_run_progress", None),
    ("invitations", None),
    ("audit_log", None),
    ("workspace_members", None),
)

JOB_TYPES: Final = run_inventory.RUN_BOUND_JOB_TYPES


def unclassified(metadata: MetaData) -> set[str]:
    """Tables of the schema that this inventory has no classification for (must be empty)."""
    return set(metadata.tables) - set(CLASSIFICATION)


def tenant_tables(metadata: MetaData) -> set[str]:
    """Tables that carry a `workspace_id` or `project_id` column, plus the two roots."""
    named = {
        table.name
        for table in metadata.tables.values()
        if "workspace_id" in table.columns or "project_id" in table.columns
    }
    return named | {"workspaces", "projects"}


def tables_of(kind: Kind) -> tuple[str, ...]:
    return tuple(name for name, entry in CLASSIFICATION.items() if entry.kind is kind)
