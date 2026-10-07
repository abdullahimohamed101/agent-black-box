"""Client-side redaction (spec §67.5, ADR-010, INV-5): nothing leaves the process unsanitized.

Pipeline, in the spec's order, applied to every event before serialization:

1. field rules: values under a denied key are replaced (`deny_keys`, minus `allow_keys`);
2. secret-pattern detector: known credential shapes inside any string are replaced;
3. user callback: `callback(event) -> event | None` gets the sanitized event for final changes;
4. payload mode: `FULL` keeps payloads, `METADATA_ONLY` (default) and `DISABLED` drop them.

The payload mode is checked before the payload is walked (the result is identical, the work is not
wasted). Redaction is a pure function of its input: the same event always produces the same output,
so rules can be tested locally with `Redactor.redact_value`. Secret detection is best effort and
cannot find every secret; keep sensitive data out of events when you can.

A callback that raises fails closed: the event is dropped (P0 lifecycle events are reduced to their
structural attributes instead, so a run is not left open forever) and the failure is counted.
"""

import re
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any

from blackbox.stats import Stats

MAX_DEPTH = 16
# Strings are bounded before scanning so a huge value cannot stall the caller. One byte over the
# inline payload limit: such a payload is later dropped as too large, never silently truncated.
MAX_STRING = 64 * 1024 + 1
Event = dict[str, Any]
Callback = Callable[[Event], "Event | None"]


class PayloadMode(str, Enum):
    FULL = "full"  # payloads captured (after redaction)
    METADATA_ONLY = "metadata_only"  # default: attributes and timings only
    DISABLED = "disabled"  # METADATA_ONLY, and no free text (error messages, run metadata)


DEFAULT_DENY_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "access_key",
)

# (kind, pattern, replacement template). `\g<1>` keeps a leading label such as "password=".
_SECRET_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str], ...] = tuple(
    (kind, re.compile(pattern), repl)
    for kind, pattern, repl in (
        (
            "private_key",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            "[REDACTED:private_key]",
        ),
        ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[REDACTED:aws_access_key]"),
        ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "[REDACTED:github_token]"),
        ("github_token", r"\bgithub_pat_[A-Za-z0-9_]{20,}", "[REDACTED:github_token]"),
        ("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "[REDACTED:slack_token]"),
        ("api_key", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}", "[REDACTED:api_key]"),
        ("api_key", r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}", "[REDACTED:api_key]"),
        ("abb_api_key", r"\babb_(?:live|test)_[A-Za-z0-9._-]{10,}", "[REDACTED:abb_api_key]"),
        (
            "jwt",
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
            "[REDACTED:jwt]",
        ),
        ("bearer_token", r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}", r"\g<1>[REDACTED:bearer]"),
        ("url_credentials", r"(://)[^/\s:@]+:[^/\s@]+@", r"\g<1>[REDACTED:url_credentials]@"),
        (
            "credential",
            r"(?i)(\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
            r"\g<1>[REDACTED:credential]",
        ),
    )
)
_MAYBE_SECRET = re.compile(r"[-_=:@.]|\d")  # cheap pre-check: plain prose skips the regex pass

# What a P0 event keeps if the user callback fails: structure, never free text.
_STRUCTURAL_KEYS = frozenset(
    {
        "span.name",
        "span.kind",
        "tool.name",
        "llm.provider",
        "llm.model",
        "approval.id",
        "policy.id",
        "retry.attempt",
        "agent.state",
        "agent.child_id",
        "shell.command",
        "run.name",
    }
)


def _norm(key: str) -> str:
    return key.lower().replace("-", "_")


class Redactor:
    def __init__(
        self,
        *,
        payload_mode: PayloadMode = PayloadMode.METADATA_ONLY,
        deny_keys: Iterable[str] = DEFAULT_DENY_KEYS,
        allow_keys: Iterable[str] = (),
        callback: Callback | None = None,
        stats: Stats | None = None,
    ) -> None:
        self.payload_mode = payload_mode
        self._deny = tuple(_norm(k) for k in deny_keys)
        self._allow = frozenset(_norm(k) for k in allow_keys)
        self._callback = callback
        self._stats = stats or Stats()

    # -- building blocks (public so rules can be unit-tested locally) ------------------------------

    def redact_string(self, text: str) -> str:
        if len(text) > MAX_STRING:
            text = text[:MAX_STRING]
        if len(text) < 8 or _MAYBE_SECRET.search(text) is None:
            return text
        for _kind, pattern, repl in _SECRET_PATTERNS:
            text, n = pattern.subn(repl, text)
            if n:
                self._stats.add("redactions", n)
        return text

    def _key_denied(self, key: str) -> bool:
        k = _norm(key)
        if k in self._allow or k.rsplit(".", 1)[-1] in self._allow:
            return False
        return any(d in k for d in self._deny)

    def redact_value(self, value: Any, key: str = "", depth: int = 0) -> Any:
        """Redact a JSON-like value. Non-JSON objects become `<TypeName>`; deep nesting is cut."""
        if value is None or isinstance(value, (bool, int, float)):
            return value  # numbers and booleans cannot carry a credential
        denied = bool(key) and self._key_denied(key)
        if denied:
            self._stats.add("redactions")
            return f"[REDACTED:{_norm(key)}]"
        if isinstance(value, str):
            return self.redact_string(value)
        if depth >= MAX_DEPTH:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            return {
                self.redact_string(str(k)): self.redact_value(v, str(k), depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact_value(v, key, depth + 1) for v in value]
        return f"<{type(value).__name__}>"

    # -- the pipeline -----------------------------------------------------------------------------

    def apply(self, event: Event, *, p0: bool = False) -> Event | None:
        """Sanitize a freshly built event in place and return it (None: drop the event)."""
        attributes: dict[str, Any] = event.get("attributes") or {}
        drop_free_text = self.payload_mode is PayloadMode.DISABLED
        clean: dict[str, Any] = {}
        for k, v in attributes.items():
            if drop_free_text and (k == "error.message" or k.startswith("metadata.")):
                continue
            clean[k] = self.redact_value(v, k)
        event["attributes"] = clean
        if event.get("tags"):
            event["tags"] = [self.redact_string(t) for t in event["tags"]]
        payload = event.get("payload")
        if payload is not None:
            if self.payload_mode is PayloadMode.FULL:
                event["payload"] = self.redact_value(payload)
            else:
                del event["payload"]
                self._stats.add("payloads_dropped")
        if "payload_ref" in event and self.payload_mode is not PayloadMode.FULL:
            del event["payload_ref"]
        if self._callback is None:
            return event
        try:
            result = self._callback(event)
        except Exception:
            self._stats.add("dropped_redaction_error")
            if not p0:
                return None
            event.pop("payload", None)
            event["attributes"] = {k: v for k, v in clean.items() if k in _STRUCTURAL_KEYS}
            return event
        if result is None:
            return None
        return result
