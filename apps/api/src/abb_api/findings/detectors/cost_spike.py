"""cost-spike: a finished run costing far more than the same agent's recent runs (probable).

The one detector that reads beyond the run: the baseline is the newest finished, summarised runs of
the same project and agent within the window (the runs module selects them). The evidence records
the baseline, so a later rebuild may legitimately differ when the baseline moved (ADR-080 (4)).
"""

from decimal import Decimal

from abb_event_schema.enums import RunStatus

from abb_api.findings.model import (
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)

THRESHOLD_NAMES = ("cost_spike_min_baseline", "cost_spike_window_days")
FINISHED = frozenset({RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.BLOCKED})
MIN_COST_USD = Decimal("0.01")
MEDIAN_FACTOR = 2
CRITICAL_FACTOR = 5


class CostSpike(Detector):
    name = "cost-spike"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        base, facts = ctx.baseline, ctx.facts
        if (
            base is None
            or facts.status not in FINISHED
            or base.n < ctx.config.cost_spike_min_baseline
        ):
            return []
        cost = facts.cost_usd
        if not (
            cost > base.p95_usd and cost > MEDIAN_FACTOR * base.median_usd and cost >= MIN_COST_USD
        ):
            return []
        critical = cost > CRITICAL_FACTOR * base.p95_usd
        return [
            self.make(
                ctx,
                finding_type="cost_spike",
                kind=Kind.PROBABLE,
                severity=Severity.CRITICAL if critical else Severity.WARNING,
                confidence=0.8 if critical else 0.6,
                start_event_id=None,
                end_event_id=None,
                key=base.agent_slug,
                evidence={
                    "cost_usd": cost,
                    "baseline": {
                        "n": base.n,
                        "median_usd": base.median_usd,
                        "p95_usd": base.p95_usd,
                        "window_days": base.window_days,
                        "newest_started_at": base.newest_started_at.isoformat(),
                        "agent_slug": base.agent_slug,
                    },
                    "thresholds": ctx.config.thresholds(*THRESHOLD_NAMES),
                },
            )
        ]
