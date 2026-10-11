"""reported-loop: an integration said the agent is looping (`loop.detected`; deterministic)."""

from abb_api.findings.model import (
    MAX_PATTERN_TOKENS,
    MAX_PER_DETECTOR,
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
    label,
)
from abb_api.findings.tokens import attr_int


class ReportedLoop(Detector):
    name = "reported-loop"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        found: list[Finding] = []
        for event in ctx.events:
            if event.event_type != "loop.detected":
                continue
            raw = event.attributes.get("loop.pattern")
            pattern = (
                [label(p) for p in raw[:MAX_PATTERN_TOKENS] if isinstance(p, str)]
                if isinstance(raw, list)
                else []
            )
            found.append(
                self.make(
                    ctx,
                    finding_type="reported_loop",
                    kind=Kind.DETERMINISTIC,
                    severity=Severity.WARNING,
                    confidence=1.0,
                    start_event_id=event.event_id,
                    end_event_id=event.event_id,
                    key="",
                    evidence={
                        "pattern": pattern,
                        "repetitions": attr_int(event, "loop.repetitions"),
                        "event_id": event.event_id,
                    },
                )
            )
            if len(found) >= MAX_PER_DETECTOR:
                break
        return found
