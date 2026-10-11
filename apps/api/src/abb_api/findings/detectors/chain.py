"""failure-chain: why a run ended badly, as an ordered list of linked events (D10), and the
heuristic causes (§80.3) that explain it.

The chain is deterministic (restates typed events); every cause is a separate `probable_cause`
finding so each carries its own confidence. Walks over span ancestry are iterative and cycle-safe.
"""

import re
from collections import defaultdict
from collections.abc import Iterable

from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event

from abb_api.findings.model import (
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
    label,
)
from abb_api.findings.tokens import attr_int, attr_str, closer_kind, is_error, tool_name

FAILED_RUNS = frozenset({RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.BLOCKED})
MAX_CHAIN = 50
MAX_ERRORS = 20
MAX_LINEAGE = 20
MAX_CAUSE_IDS = 20
MUTATIONS = frozenset({"file.created", "file.modified", "file.deleted", "git.commit", "git.push"})
_GOOD_CLOSERS = ("tool.call.completed", "shell.command.completed", "db.query.completed")
_WORDS = re.compile(r"[^a-z]+")

# Whole-word phrases (words are runs of letters, lower-cased): `invalid_token` is ["invalid",
# "token"], which matches nothing; a bare "token" never counts.
_OVERFLOW = (
    ("context", "length"),
    ("context", "window"),
    ("max", "tokens"),
    ("too", "long"),
    ("prompt", "too", "long"),
    ("content", "too", "large"),
)
_BAD_ARGUMENTS = (
    ("invalid", "argument"),
    ("invalid", "arguments"),
    ("validation", "error"),
    ("schema", "error"),
    ("invalid", "param"),
    ("invalid", "params"),
)


def _has_phrase(value: str | None, phrases: Iterable[tuple[str, ...]]) -> bool:
    if not value:
        return False
    words = [w for w in _WORDS.split(value[:256].lower()) if w]
    return any(
        tuple(words[i : i + len(p)]) == p for p in phrases for i in range(len(words) - len(p) + 1)
    )


def _succeeded(event: Event) -> bool:
    return event.status is None or event.status.value == "success"


def _lineage(ctx: DetectorContext, span_id: str | None) -> list[str]:
    """The span and its ancestors, nearest first; a cycle ends the walk."""
    seen: dict[str, None] = {}
    node = span_id
    while node is not None and node not in seen:
        seen[node] = None
        span = ctx.spans.get(node)
        node = span.parent_span_id if span else None
    return list(seen)


def _category(ctx: DetectorContext, root: Event) -> str:
    kind = root.event_type
    if ctx.facts.status is RunStatus.BLOCKED or kind == "policy.action.blocked":
        return "policy_termination"
    if (
        ctx.facts.status is RunStatus.TIMED_OUT
        or kind == "timeout.occurred"
        or (root.status is not None and root.status.value == "timeout")
    ):
        return "timeout"
    if kind.startswith("shell.command"):
        return "command_failure"
    if kind.startswith("db.query") or kind == "http.response":
        return "external_service_failure"
    if kind.startswith("llm.request"):
        return "provider_failure"
    if kind.startswith("tool.call"):
        return "tool_failure"
    return "unknown"


class FailureChain(Detector):
    name = "failure-chain"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        events, pos = ctx.events, ctx.position
        terminal = self._terminal(ctx)
        if terminal is None:
            return []
        end = pos[terminal.event_id]
        # The terminal event (`run.failed`) usually names no span, so the chain is anchored at the
        # last error before it, whose span lineage then says where the failure came from.
        anchor = terminal
        if terminal.span_id is None:
            anchor = next(
                (
                    e
                    for e in reversed(events[:end])
                    if is_error(e) and not e.event_type.startswith("run.")
                ),
                terminal,
            )
        lineage = _lineage(ctx, anchor.span_id)
        on_lineage = set(lineage)
        errors = [
            i for i in range(end + 1) if events[i].span_id in on_lineage and is_error(events[i])
        ]
        root_at = errors[0] if errors else pos[anchor.event_id]
        root = events[root_at]
        good_at = next(
            (
                i
                for i in range(root_at - 1, -1, -1)
                if events[i].event_type in _GOOD_CLOSERS and _succeeded(events[i])
            ),
            -1,
        )
        mutations = [i for i in range(good_at + 1, end + 1) if events[i].event_type in MUTATIONS]
        chain_ids = {terminal.event_id, root.event_id, *(events[i].event_id for i in errors)}
        retries = [
            i
            for i in range(end + 1)
            if events[i].event_type == "retry.attempted"
            and (
                events[i].span_id in on_lineage
                or attr_str(events[i], "retry.of_event_id") in chain_ids
            )
        ]

        picked: dict[int, str] = {}
        picked.setdefault(end, "terminal")
        picked.setdefault(root_at, "root")
        for i in errors:
            if i > root_at and i != end and len(picked) < MAX_CHAIN:
                picked.setdefault(i, "error")
        for role, indexes in (("retry", retries), ("mutation", mutations)):
            for i in indexes:
                if len(picked) < MAX_CHAIN:
                    picked.setdefault(i, role)
        chain = [
            {
                "event_id": events[i].event_id,
                "event_type": label(events[i].event_type),
                "role": role,
                "span_id": events[i].span_id,
            }
            for i, role in sorted(picked.items())
        ]
        failed = ctx.facts.status in FAILED_RUNS
        found = [
            self.make(
                ctx,
                finding_type="failure_chain",
                kind=Kind.DETERMINISTIC,
                severity=Severity.CRITICAL if failed else Severity.WARNING,
                confidence=1.0,
                start_event_id=root.event_id,
                end_event_id=terminal.event_id,
                key="chain",
                evidence={
                    "category": _category(ctx, root),
                    "terminal_event_id": terminal.event_id,
                    "root_event_id": root.event_id,
                    "chain": chain,
                    "span_lineage": lineage[:MAX_LINEAGE],
                    "retries_caused": len(retries),
                    "retries_unattributed": ctx.facts.retries_unattributed,
                    "mutations": len(mutations),
                },
            )
        ]
        found.extend(self._causes(ctx, end, good_at))
        return found

    def _terminal(self, ctx: DetectorContext) -> Event | None:
        facts, events = ctx.facts, ctx.events
        if facts.status in FAILED_RUNS and facts.terminal_event_id is not None:
            return events[ctx.position[facts.terminal_event_id]]
        failed = [e for e in events if e.event_type == "run.failed"]
        if not failed:
            return None
        errors = [e for e in events if is_error(e) and not e.event_type.startswith("run.")]
        return errors[-1] if errors else failed[-1]

    def _causes(self, ctx: DetectorContext, end: int, good_at: int) -> list[Finding]:
        events = ctx.events[: end + 1]
        found: list[Finding] = []

        def cause(
            name: str, confidence: float, picked: list[Event], **extra: str | None
        ) -> Finding:
            return self.make(
                ctx,
                finding_type="probable_cause",
                kind=Kind.PROBABLE,
                severity=Severity.WARNING,
                confidence=confidence,
                start_event_id=picked[0].event_id,
                end_event_id=picked[-1].event_id,
                key=name + "\x1f" + "\x1f".join(str(v) for v in extra.values()),
                evidence={
                    "cause": name,
                    "event_ids": [e.event_id for e in picked[:MAX_CAUSE_IDS]],
                    **{k: v for k, v in extra.items() if v is not None},
                },
            )

        # A file changed, then the tests failed, inside the chain's window.
        last_change: Event | None = None
        for e in events[good_at + 1 :]:
            if e.event_type in ("file.modified", "file.created"):
                last_change = e
            elif (
                last_change is not None
                and closer_kind(e) == "shell.command"
                and (attr_int(e, "test.failed") or 0) > 0
            ):
                found.append(cause("code_change_before_test_failure", 0.5, [last_change, e]))
                break

        span_tool: dict[str, str] = {}
        for e in events:
            name = tool_name(e)
            if name is not None and e.span_id is not None and e.event_type.startswith("tool.call"):
                span_tool.setdefault(e.span_id, name)
        timeouts: dict[str, list[Event]] = defaultdict(list)
        bad_arguments: dict[str, list[Event]] = defaultdict(list)
        overflow: list[Event] = []
        for e in events:
            tool = tool_name(e)
            if e.event_type == "timeout.occurred" and e.span_id in span_tool:
                timeouts[span_tool[e.span_id]].append(e)
            elif closer_kind(e) == "tool.call" and tool is not None:
                if e.status is not None and e.status.value == "timeout":
                    timeouts[tool].append(e)
                if e.event_type.endswith(".failed") and _has_phrase(
                    attr_str(e, "tool.error_type"), _BAD_ARGUMENTS
                ):
                    bad_arguments[tool].append(e)
            elif e.event_type == "llm.request.failed" and _has_phrase(
                attr_str(e, "llm.error_type"), _OVERFLOW
            ):
                overflow.append(e)
        for tool in sorted(timeouts):
            if len(timeouts[tool]) >= 2:
                found.append(cause("repeated_tool_timeout", 0.7, timeouts[tool], tool_name=tool))
        if overflow:
            model = attr_str(overflow[0], "llm.model")
            found.append(
                cause(
                    "context_overflow",
                    0.7,
                    overflow,
                    model=label(model) if model is not None else None,
                )
            )
        for tool in sorted(bad_arguments):
            found.append(cause("invalid_tool_arguments", 0.6, bad_arguments[tool], tool_name=tool))
        return found
