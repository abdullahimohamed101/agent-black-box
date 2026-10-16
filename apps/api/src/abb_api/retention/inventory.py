"""Which tables hold a run's data, and what retention does with each (ADR-160, fail-closed).

Every table that carries a run (a `run_id` column, or a foreign key to `runs` or `events`) is
classified here, and `tests/retention/test_inventory.py` fails for one that is not: a table added
later cannot silently survive (or silently escape) retention. The deletion procedure is driven by
these lists, so the classification is the behaviour, not documentation of it.

* DETAIL: removed when the run's tier expires (artifacts tier or trace tier).
* RECORD: part of the run record; removed (or cascaded) with it by the run tier.
* EXEMPT: deliberately not touched by the run tiers; the reason is stated.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from abb_api.jobs.outbox import DETECT_FINDINGS, EVALUATE_RUN, SUMMARIZE_RUN


class Kind(StrEnum):
    DETAIL = "retained-detail"
    RECORD = "retained-record"
    EXEMPT = "exempt"


@dataclass(frozen=True)
class Entry:
    kind: Kind
    note: str


# Deleted in this order by the Postgres detail step, in batches (events first: they are the
# bulk, and the run row's RESTRICT foreign key needs them gone before the record can go).
DETAIL_BATCHED: Final = (
    "events",
    "spans",
    "cost_calculations",
    "findings",
    "run_findings",
    "evaluations",
)
SEARCH_TABLES: Final = ("run_search",)  # the search index step
ARTIFACT_TABLES: Final = ("artifacts",)  # the artifact store step (files, then rows)
# Run-bound background jobs name the run in their coalescing key (`<workspace>:<run>`).
RUN_BOUND_JOB_TYPES: Final = (SUMMARIZE_RUN, DETECT_FINDINGS, EVALUATE_RUN)

CLASSIFICATION: Final[dict[str, Entry]] = {
    "events": Entry(Kind.DETAIL, "trace tier, batched"),
    "spans": Entry(Kind.DETAIL, "trace tier, batched"),
    "cost_calculations": Entry(Kind.DETAIL, "trace tier, batched"),
    "findings": Entry(Kind.DETAIL, "trace tier, batched"),
    "run_findings": Entry(Kind.DETAIL, "trace tier, batched"),
    "evaluations": Entry(Kind.DETAIL, "trace tier, batched"),
    "run_search": Entry(Kind.DETAIL, "trace tier, the search step"),
    "artifacts": Entry(Kind.DETAIL, "artifacts tier: files through the ArtifactStore, then rows"),
    "outbox_jobs": Entry(Kind.DETAIL, "trace tier: jobs of RUN_BOUND_JOB_TYPES keyed to the run"),
    "runs": Entry(Kind.RECORD, "run tier, last; trace_state marks the stages before"),
    "experiment_runs": Entry(Kind.RECORD, "membership row, cascades with the run record"),
    "analytics_top_runs": Entry(Kind.RECORD, "rollup; rebuilt for the day after the run goes"),
    "approvals": Entry(
        Kind.EXEMPT,
        "governance record without a foreign key to the run; scrubbed arguments outlive the "
        "trace tier (KI-195)",
    ),
    "alert_notifications": Entry(Kind.EXEMPT, "pruned by its own delivery-log window (alerts)"),
    "retention_tombstones": Entry(Kind.EXEMPT, "the permanent record that a run was removed"),
    "retention_run_progress": Entry(Kind.EXEMPT, "the job's own working state, deleted when done"),
}

# Column names that make a table "run-bound" for the classification test.
RUN_COLUMN_NAMES: Final = ("run_id", "consumed_run_id")
RUN_REFERENCED_TABLES: Final = ("runs", "events")


def tables_of(kind: Kind) -> tuple[str, ...]:
    return tuple(name for name, entry in CLASSIFICATION.items() if entry.kind is kind)
