"""Trace-context propagation across processes (spec §67.2, ADR-070).

A `TraceContext` names the run, the trace, the span the receiver should parent to and the agent
id the receiver runs as. It travels as one short string:

    abb1;run=run_<ULID>;trace=trc_<ULID>;span=spn_<ULID>;agent=<slug>      (span, agent optional)

in the environment variable `BLACKBOX_TRACE_CONTEXT` (`trace_context_from_env()`), or in an HTTP
header named `X-Blackbox-Trace-Context` (the SDK only exports the name; it does no HTTP for you).
The string is not a credential, only an address: treat it like a request id. The receiver still
needs an API key of the same project.

Parsing is strict and bounded, because the string comes from an environment or a network: any
deviation yields `None` and a log line that never contains the value (INV-4, INV-5).
"""

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("agent_blackbox")

TRACE_CONTEXT_ENV = "BLACKBOX_TRACE_CONTEXT"
TRACE_CONTEXT_HEADER = "X-Blackbox-Trace-Context"
MAX_CONTEXT_LENGTH = 256
_VERSION = "abb1"
_ULID = r"[0-7][0-9A-HJKMNP-TV-Z]{25}"  # the contract's id pattern (abb_event_schema.ids)
_PATTERNS = {
    "run": re.compile(rf"run_{_ULID}"),
    "trace": re.compile(rf"trc_{_ULID}"),
    "span": re.compile(rf"spn_{_ULID}"),
    "agent": re.compile(r"[a-z0-9][a-z0-9._-]{0,63}"),
}


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    trace_id: str
    parent_span_id: str | None = None  # the span the receiver's root spans nest under
    agent_id: str | None = None  # the agent identity the receiver runs as

    def serialize(self) -> str:
        parts = [_VERSION, f"run={self.run_id}", f"trace={self.trace_id}"]
        if self.parent_span_id:
            parts.append(f"span={self.parent_span_id}")
        if self.agent_id:
            parts.append(f"agent={self.agent_id}")
        return ";".join(parts)

    @classmethod
    def parse(cls, value: object) -> "TraceContext | None":
        """The context for a well-formed string, else None (never raises, never logs the value)."""
        context = _parse(value)
        if context is None:
            log.warning("blackbox: ignoring an invalid trace context")
        return context


def _parse(value: object) -> TraceContext | None:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_CONTEXT_LENGTH:
        return None  # checked first: a megabyte string is never split
    pieces = value.split(";")
    if pieces[0] != _VERSION or not 3 <= len(pieces) <= 5:
        return None
    fields: dict[str, str] = {}
    for piece in pieces[1:]:
        key, sep, text = piece.partition("=")
        pattern = _PATTERNS.get(key)
        if not sep or pattern is None or key in fields or not pattern.fullmatch(text):
            return None
        fields[key] = text
    if "run" not in fields or "trace" not in fields:
        return None
    return TraceContext(fields["run"], fields["trace"], fields.get("span"), fields.get("agent"))


def trace_context_from_env(environ: Mapping[str, str] | None = None) -> TraceContext | None:
    """The context a parent process left in `BLACKBOX_TRACE_CONTEXT`, or None (absent or invalid).

    The variable is inherited by every descendant that does not overwrite or remove it.
    """
    try:
        value = (os.environ if environ is None else environ).get(TRACE_CONTEXT_ENV)
    except Exception:
        return None
    return None if value is None else TraceContext.parse(value)
