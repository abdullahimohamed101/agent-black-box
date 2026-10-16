"""Retention metrics: a finished tick, translated into catalogue counters (Phase 19c step 2).

The retention runner is its own process, so these land in a registry that is written to a textfile
(`obs/textfile.py`) for node_exporter. Everything here is a count or a fixed word.
"""

from abb_api.obs.catalogue import RETENTION_MODES, RETENTION_TIERS, Catalogue
from abb_api.retention.tick import TickResult

# Tiers that count runs; the rest count rows. The dry-run gauge reports each in its own unit.
_RUN_TIERS = frozenset({"artifacts", "trace", "run"})


def record_tick(cat: Catalogue, result: TickResult) -> None:
    """Count one finished tick. A tick that another process held the lock of did nothing and is
    not a pass."""
    if result.locked_out:
        return
    cat.retention_passes_total.inc(result.mode)
    cat.retention_mode.replace({(m,): 1.0 if m == result.mode else 0.0 for m in RETENTION_MODES})
    if result.mode == "enforce":
        for tier, tally in result.tiers.items():
            for kind, amount in (
                ("runs", tally.runs),
                ("events", tally.events),
                ("rows", tally.rows),
                ("bytes", tally.artifact_bytes),
            ):
                if amount:
                    cat.retention_deleted_total.inc(tier, kind, amount=float(amount))
    elif result.mode == "dry_run":
        cat.retention_dry_run_candidates.replace(
            {
                (tier,): float(tally.runs if tier in _RUN_TIERS else tally.rows)
                for tier, tally in result.tiers.items()
                if tier in RETENTION_TIERS
            }
        )
    for code, amount in (
        ("measure_error", result.errors),
        ("step_failed", result.failed),
        ("refused", 1 if result.refused else 0),
        ("budget_exhausted", 1 if result.budget_exhausted else 0),
    ):
        if amount:
            cat.retention_failures_total.inc(code, amount=float(amount))


def record_crash(cat: Catalogue) -> None:
    """A tick that raised (for instance the database restarted)."""
    cat.retention_failures_total.inc("tick_crashed")
