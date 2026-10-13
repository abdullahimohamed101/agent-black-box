"""Titles and messages, generated at read time from typed evidence (never stored).

The wording lives here and nowhere else. Only evidence values reach the text, and evidence is
metadata by construction (ADR-080), so a viewer and an owner read the same sentences.
"""

from typing import Any, Protocol

from abb_api.findings.model import Kind, label
from abb_api.findings.security_words import BY_FINDING_TYPE, KIND_WORDS

CATEGORY_WORDS = {
    "command_failure": "a command failed",
    "external_service_failure": "an external service failed",
    "provider_failure": "the model provider failed",
    "policy_termination": "a policy stopped it",
    "timeout": "an operation timed out",
    "tool_failure": "a tool failed",
    "unknown": "the cause is not known",
}
CAUSE_WORDS = {
    "code_change_before_test_failure": "a code change was followed by failing tests",
    "repeated_tool_timeout": "a tool timed out repeatedly",
    "context_overflow": "the model's context limit was reached",
    "invalid_tool_arguments": "a tool was called with invalid arguments",
}


class Describable(Protocol):
    @property
    def finding_type(self) -> str: ...
    @property
    def kind(self) -> Kind | str: ...
    @property
    def evidence(self) -> dict[str, Any]: ...


def _seconds(ms: Any) -> str:
    if not isinstance(ms, (int, float)):
        return "an unknown time"
    return f"{ms / 1000:.0f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _plural(n: Any, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _times(n: Any) -> str:
    """`3 times`; words when the integration omitted `loop.repetitions` (it is optional)."""
    if isinstance(n, bool) or not isinstance(n, int):
        return "an unknown number of times"
    return _plural(n, "time")


def _text(value: Any, fallback: str = "?") -> str:
    return label(value) if isinstance(value, str) else fallback


def _pattern(value: Any) -> str:
    items = value if isinstance(value, list) else []
    return " -> ".join(_text(p) for p in items) or "?"


def describe(finding: Describable) -> tuple[str, str]:
    """(title, message). Probable findings are titled "Probable: ..."."""
    e = finding.evidence
    kind = finding.kind
    probable = (kind.value if isinstance(kind, Kind) else kind) == Kind.PROBABLE.value
    title, message = _words(finding.finding_type, e)
    return (f"Probable: {title}" if probable else title), message


def _words(finding_type: str, e: dict[str, Any]) -> tuple[str, str]:
    if finding_type == "possible_loop":
        return (
            "the agent may be looping",
            f"The agent repeated {_pattern(e.get('pattern'))}"
            f" {_times(e.get('repetitions'))} in {_seconds(e.get('duration_ms'))}.",
        )
    if finding_type == "identical_request":
        what = (
            f"Tool {_text(e.get('tool_name'))} was called"
            if e.get("event_type") == "tool.call.started"
            else "The model was called"
        )
        return (
            "identical requests repeated",
            f"{what} {_plural(e.get('count'), 'time')} with an identical payload.",
        )
    if finding_type == "retry_storm":
        shape = "in a burst on one operation" if e.get("burst") else "spread over the run"
        return (
            "retry storm",
            f"{_plural(e.get('retries'), 'retry')} {shape}"
            f" ({_plural(e.get('rate_limit_hits'), 'rate-limit hit')}).",
        )
    if finding_type == "context_growth":
        pace = ", faster and faster" if e.get("superlinear") else ""
        return (
            "the context is growing fast",
            f"Input grew from {e.get('first_tokens')} to {e.get('last_tokens')} tokens over"
            f" {_plural(e.get('calls'), 'model call')}, about {e.get('mean_delta_tokens')} tokens"
            f" per call{pace}.",
        )
    if finding_type == "cost_spike":
        base = e.get("baseline") or {}
        return (
            "unusually expensive run",
            f"This run cost ${e.get('cost_usd')}; the agent's p95 over its last"
            f" {base.get('n')} runs is ${base.get('p95_usd')} (median ${base.get('median_usd')}).",
        )
    if finding_type == "tool_failure_cascade":
        failures = e.get("failures") or []
        alt = e.get("alternates") or []
        tail = f" It then used {', '.join(_text(a) for a in alt)}." if alt else ""
        return (
            "failures in a row",
            f"{_plural(len(failures), 'failure')} in {_plural(e.get('calls_in_window'), 'call')},"
            f" with {_plural(e.get('retries_between'), 'retry')} between.{tail}",
        )
    if finding_type == "failure_chain":
        return (
            "the run failed: "
            + CATEGORY_WORDS.get(_text(e.get("category")), "the cause is unknown"),
            f"{_plural(e.get('retries_caused'), 'retry')} followed the first error, and"
            f" {_plural(e.get('mutations'), 'change')} to files or the repository were made before"
            " the end.",
        )
    if finding_type == "probable_cause":
        cause = CAUSE_WORDS.get(_text(e.get("cause")), "an unclassified cause")
        tool = e.get("tool_name")
        return cause, f"This may explain the failure{f' (tool {_text(tool)})' if tool else ''}."
    if finding_type == "reported_loop":
        return (
            "loop reported by the integration",
            f"The integration reported {_pattern(e.get('pattern'))}"
            f" repeated {_times(e.get('repetitions'))}.",
        )
    security = BY_FINDING_TYPE.get(finding_type)
    if security is not None:
        # Fixed sentences (ADR-131). The only variable part is a credential kind, looked up in a
        # closed vocabulary: evidence text itself is never repeated.
        message = security.message
        if finding_type == "credential_in_output":
            kinds = e.get("kinds")
            named = (
                sorted(KIND_WORDS[k] for k in kinds if k in KIND_WORDS)
                if isinstance(kinds, list)
                else []
            )
            if named:
                message = (
                    f"Captured text contained a credential ({', '.join(named)}); "
                    "it was redacted before it was stored."
                )
        return security.title, message
    return "finding", "A detector reported something."
