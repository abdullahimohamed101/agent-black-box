"""context-growth: the prompt grows fast (a rate, not a ratio; probable).

An ordinary multi-turn agent's input grows linearly by construction, so growth alone is not a
finding. A non-decreasing stretch of `context_growth_min_calls` model calls is reported when the
mean increment is at least `context_growth_min_delta_tokens`, the last value is at least
`context_growth_factor` times the first, and either the increments themselves increase (the
context compounds) or the last value reaches `context_growth_warning_tokens`.
"""

from itertools import pairwise

from abb_event_schema.event import Event

from abb_api.findings.model import (
    MAX_IDS,
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)
from abb_api.findings.tokens import attr_int, by_agent

THRESHOLD_NAMES = (
    "context_growth_min_calls",
    "context_growth_min_delta_tokens",
    "context_growth_factor",
    "context_growth_warning_tokens",
)
CRITICAL_TOKENS = 150_000
SUPERLINEAR_RATIO = 1.25  # second-half mean increment over first-half mean increment


def _sample(count: int, limit: int) -> list[int]:
    """Indexes spread evenly over `count` items, always including the first and the last."""
    if count <= limit:
        return list(range(count))
    return sorted({round(i * (count - 1) / (limit - 1)) for i in range(limit)})


class ContextGrowth(Detector):
    name = "context-growth"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        found: list[Finding] = []
        for agent, events in by_agent(ctx.events).items():
            series: list[tuple[Event, int]] = []
            for event in events:
                if event.event_type != "llm.request.completed":
                    continue
                tokens = attr_int(event, "llm.input_tokens")
                if tokens is not None and tokens >= 0:
                    series.append((event, tokens))
            found.extend(self._stretches(ctx, agent, series))
        return found

    def _stretches(
        self, ctx: DetectorContext, agent: str, series: list[tuple[Event, int]]
    ) -> list[Finding]:
        found: list[Finding] = []
        begin = 0
        for i in range(1, len(series) + 1):
            if i < len(series) and series[i][1] >= series[i - 1][1]:
                continue
            stretch = series[begin:i]
            begin = i
            finding = self._one(ctx, agent, stretch)
            if finding is not None:
                found.append(finding)
        return found

    def _one(
        self, ctx: DetectorContext, agent: str, stretch: list[tuple[Event, int]]
    ) -> Finding | None:
        cfg = ctx.config
        calls = len(stretch)
        if calls < cfg.context_growth_min_calls:
            return None
        values = [v for _, v in stretch]
        first, last = values[0], values[-1]
        mean_delta = (last - first) / (calls - 1)
        if mean_delta < cfg.context_growth_min_delta_tokens:
            return None
        if last < cfg.context_growth_factor * first:
            return None
        deltas = [b - a for a, b in pairwise(values)]
        half = len(deltas) // 2
        early, late = deltas[:half] or deltas, deltas[half:] or deltas
        superlinear = sum(late) / len(late) > SUPERLINEAR_RATIO * (sum(early) / len(early))
        if not superlinear and last < cfg.context_growth_warning_tokens:
            return None
        if last >= CRITICAL_TOKENS:
            severity = Severity.CRITICAL
        elif last >= cfg.context_growth_warning_tokens:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO
        start, end = stretch[0][0], stretch[-1][0]
        return self.make(
            ctx,
            finding_type="context_growth",
            kind=Kind.PROBABLE,
            severity=severity,
            confidence=0.85 if calls >= 10 else 0.7,
            start_event_id=start.event_id,
            end_event_id=end.event_id,
            key=agent,
            evidence={
                "agent_id": agent,
                "calls": calls,
                "first_tokens": first,
                "last_tokens": last,
                "growth_factor": round(last / first, 2) if first else None,
                "mean_delta_tokens": round(mean_delta, 1),
                "superlinear": superlinear,
                "series": [
                    {"event_id": stretch[i][0].event_id, "input_tokens": stretch[i][1]}
                    for i in _sample(calls, MAX_IDS)
                ],
                "start_event_id": start.event_id,
                "end_event_id": end.event_id,
                "thresholds": cfg.thresholds(*THRESHOLD_NAMES),
            },
        )
