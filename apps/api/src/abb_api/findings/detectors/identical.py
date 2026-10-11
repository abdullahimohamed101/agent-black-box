"""identical-request: the same request payload sent again and again (deterministic).

Only events with an inline payload take part (capture is opt-in, INV-5: no payload is not a
finding). The SHA-256 of the canonical payload groups events in memory and goes nowhere else: it
is not in the evidence and not in the fingerprint key, because every other fingerprint input is
public in the same response and a digest in the key would let a reader test a guessed payload
offline (security review P1-1). A group is identified by its first event, which is unique per group.
"""

import hashlib
import json

from abb_api.findings.model import (
    MAX_PER_DETECTOR,
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)
from abb_api.findings.tokens import tool_name

THRESHOLD_NAMES = ("identical_min_count",)
_TYPES = ("tool.call.started", "llm.request.started")


class IdenticalRequest(Detector):
    name = "identical-request"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        groups: dict[tuple[str, str, str, bytes], list[str]] = {}
        for event in ctx.events:
            if event.event_type not in _TYPES or not event.payload:
                continue
            name = tool_name(event) or "" if event.event_type == _TYPES[0] else ""
            canonical = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8", "replace")).digest()
            groups.setdefault((event.agent_id, event.event_type, name, digest), []).append(
                event.event_id
            )
        qualifying = [
            (key, ids) for key, ids in groups.items() if len(ids) >= ctx.config.identical_min_count
        ]
        qualifying.sort(key=lambda item: (-len(item[1]), ctx.position[item[1][0]]))
        found: list[Finding] = []
        for (agent, event_type, name, _digest), ids in qualifying[:MAX_PER_DETECTOR]:
            evidence: dict[str, object] = {
                "agent_id": agent,
                "event_type": event_type,
                "count": len(ids),
                "event_ids": ids,
                "thresholds": ctx.config.thresholds(*THRESHOLD_NAMES),
            }
            if event_type == _TYPES[0]:
                evidence["tool_name"] = name
            found.append(
                self.make(
                    ctx,
                    finding_type="identical_request",
                    kind=Kind.DETERMINISTIC,
                    severity=Severity.WARNING,
                    confidence=1.0,
                    start_event_id=ids[0],
                    end_event_id=ids[-1],
                    key=f"{agent}\x1f{event_type}\x1f{name}",
                    evidence=evidence,
                )
            )
        return found
