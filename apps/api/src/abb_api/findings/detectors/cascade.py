"""tool-failure-cascade: several failures among the last few calls (deterministic).

Entries are closers of tool calls, shell commands and database queries, plus HTTP responses with a
5xx status, per emitter. A window of `cascade_window_calls` entries holding at least
`cascade_min_failures` failures marks the span between its first and last failure; overlapping
windows merge into one stretch.
"""

from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event

from abb_api.findings.model import (
    MAX_PER_DETECTOR,
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
    label,
)
from abb_api.findings.tokens import attr_int, attr_str, by_agent, closer_kind, is_error, tool_name

THRESHOLD_NAMES = ("cascade_min_failures", "cascade_window_calls")
FAILED_RUNS = frozenset({RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.BLOCKED})
_CALLS = ("tool.call", "shell.command", "db.query")
ALTERNATES_LOOKAHEAD = 5


def _is_entry(event: Event) -> bool:
    if event.event_type == "http.response":
        status = attr_int(event, "http.status_code")
        return status is not None and status >= 500
    return closer_kind(event) in _CALLS


def _failed(event: Event) -> bool:
    return event.event_type == "http.response" or is_error(event)


class ToolFailureCascade(Detector):
    name = "tool-failure-cascade"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        found: list[Finding] = []
        for agent, events in by_agent(ctx.events).items():
            entries = [e for e in events if _is_entry(e)]
            found.extend(self._agent(ctx, agent, events, entries))
        found.sort(
            key=lambda f: (-len(f.evidence["failures"]), ctx.position[f.start_event_id or ""])
        )
        return found[:MAX_PER_DETECTOR]

    def _agent(
        self, ctx: DetectorContext, agent: str, events: list[Event], entries: list[Event]
    ) -> list[Finding]:
        cfg = ctx.config
        k, window = cfg.cascade_min_failures, cfg.cascade_window_calls
        failing = [i for i, e in enumerate(entries) if _failed(e)]
        stretches: list[list[int]] = []
        for j in range(len(failing) - k + 1):
            first, last = failing[j], failing[j + k - 1]
            if last - first > window - 1:
                continue
            if stretches and first <= stretches[-1][1]:
                stretches[-1][1] = max(stretches[-1][1], last)
            else:
                stretches.append([first, last])
        return [self._finding(ctx, agent, events, entries, a, b) for a, b in stretches]

    def _finding(
        self,
        ctx: DetectorContext,
        agent: str,
        events: list[Event],
        entries: list[Event],
        a: int,
        b: int,
    ) -> Finding:
        failures = [e for e in entries[a : b + 1] if _failed(e)]
        first, last = failures[0], failures[-1]
        low, high = ctx.position[first.event_id], ctx.position[last.event_id]
        failing_tools = {tool_name(e) for e in failures}
        alternates: dict[str, None] = {}
        for e in entries[a + 1 : b + 1 + ALTERNATES_LOOKAHEAD]:
            name = tool_name(e)
            if closer_kind(e) == "tool.call" and not _failed(e) and name not in failing_tools:
                alternates.setdefault(name or "?", None)
        failed_run = ctx.facts.status in FAILED_RUNS
        return self.make(
            ctx,
            finding_type="tool_failure_cascade",
            kind=Kind.DETERMINISTIC,
            severity=Severity.CRITICAL if failed_run else Severity.WARNING,
            confidence=1.0,
            start_event_id=first.event_id,
            end_event_id=last.event_id,
            key=agent,
            evidence={
                "agent_id": agent,
                "failures": [
                    {
                        "event_id": e.event_id,
                        "event_type": label(e.event_type),
                        "tool_name": tool_name(e),
                        "error_type": _label_or_none(attr_str(e, "tool.error_type")),
                        "exit_code": attr_int(e, "shell.exit_code"),
                        "status_code": attr_int(e, "http.status_code"),
                    }
                    for e in failures[:20]
                ],
                "calls_in_window": b - a + 1,
                "retries_between": sum(
                    1
                    for e in events
                    if e.event_type == "retry.attempted" and low < ctx.position[e.event_id] < high
                ),
                "alternates": list(alternates)[:10],
                "start_event_id": first.event_id,
                "end_event_id": last.event_id,
                "thresholds": ctx.config.thresholds(*THRESHOLD_NAMES),
            },
        )


def _label_or_none(value: str | None) -> str | None:
    return label(value) if value is not None else None
