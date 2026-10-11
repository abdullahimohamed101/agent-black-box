"""retry-storm: retries piling up, in a burst on one operation or in sheer number (deterministic).

Per emitter. The burst rule needs `retry_burst_min` retries naming the same span within
`retry_burst_window_seconds` of `occurred_at` (best effort under clock skew between emitters,
KI-095); the absolute rule is `retry_storm_min` retries in the run, which alone is only `info`.
"""

from abb_event_schema.event import Event

from abb_api.findings.model import (
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)
from abb_api.findings.tokens import by_agent

THRESHOLD_NAMES = ("retry_storm_min", "retry_burst_min", "retry_burst_window_seconds")


def _burst(times: list[float], minimum: int, window: float) -> bool:
    times = sorted(times)
    return any(times[i + minimum - 1] - times[i] <= window for i in range(len(times) - minimum + 1))


class RetryStorm(Detector):
    name = "retry-storm"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        cfg = ctx.config
        found: list[Finding] = []
        for agent, events in by_agent(ctx.events).items():
            retries = [e for e in events if e.event_type == "retry.attempted"]
            if not retries:
                continue
            scopes: dict[str, list[Event]] = {}
            for retry in retries:
                if retry.span_id is not None:
                    scopes.setdefault(retry.span_id, []).append(retry)
            burst = any(
                _burst(
                    [e.occurred_at.timestamp() for e in group],
                    cfg.retry_burst_min,
                    cfg.retry_burst_window_seconds,
                )
                for group in scopes.values()
            )
            if not burst and len(retries) < cfg.retry_storm_min:
                continue
            ranked = sorted(scopes.items(), key=lambda item: (-len(item[1]), item[0]))
            if burst:
                severity = Severity.CRITICAL if len(retries) >= 15 else Severity.WARNING
            else:
                severity = Severity.INFO
            found.append(
                self.make(
                    ctx,
                    finding_type="retry_storm",
                    kind=Kind.DETERMINISTIC,
                    severity=severity,
                    confidence=1.0,
                    start_event_id=retries[0].event_id,
                    end_event_id=retries[-1].event_id,
                    key=agent,
                    evidence={
                        "agent_id": agent,
                        "retries": len(retries),
                        "retries_unattributed": len(retries) - sum(map(len, scopes.values())),
                        "burst": burst,
                        "by_scope": [
                            {
                                "span_id": span,
                                "count": len(group),
                                "first_event_id": group[0].event_id,
                                "last_event_id": group[-1].event_id,
                            }
                            for span, group in ranked[:20]
                        ],
                        "rate_limit_hits": sum(
                            1 for e in events if e.event_type == "rate_limit.hit"
                        ),
                        "window_seconds": cfg.retry_burst_window_seconds,
                        "thresholds": cfg.thresholds(*THRESHOLD_NAMES),
                    },
                )
            )
        return found
