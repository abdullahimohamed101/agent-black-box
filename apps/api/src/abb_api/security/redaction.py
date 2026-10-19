"""Server-side redaction and capture modes, applied before an event or artifact is stored.

Accepted events are immutable (INV-1), so this is the only moment the server can keep a secret out
of storage: the event is changed *before* it is accepted, deterministically, so redelivering the
same event produces the same stored event (idempotent persistence on `(workspace_id, event_id)`).
The rules, per the project's capture mode (ADR-130):

- FULL: every string the event carries (attributes, tags, the inline payload) is scanned; secret
  shapes become `[REDACTED:<label>]`; the event records how many and which kinds were found
  (also those inside a payload that is then dropped: the signal matters more than the bytes).
- METADATA_ONLY: the inline payload, `payload_ref` and artifact-reference attributes are dropped,
  whatever the SDK sent. Other attributes (command, path ...) are metadata in the SDK's sense and
  stay; they are still scanned.
- DISABLED: METADATA_ONLY, plus no free text (`error.message`, `metadata.*`), exactly what the
  SDK's DISABLED mode drops.

Fail closed: when a value cannot be scanned (error, too large, out of time) it is dropped and the
event says why; the event itself is kept with its metadata. Client-supplied `redaction.*` markers
are discarded, so the markers always describe what this server did.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from abb_event_schema import limits
from abb_event_schema.event import Event
from abb_event_schema.registry import (
    PAYLOAD_REFERENCE_ATTRIBUTES,
    SERVER_REDACTION_ATTRIBUTES,
    lookup,
)

from abb_api.security.scanner import (
    DEFAULT_MAX_SECONDS,
    DENIED_KEY_KIND,
    BytesScanResult,
    ScanBudget,
    ScanFailed,
    ScanTooLarge,
    SecretScanner,
)
from abb_api.security.settings import CaptureMode

logger = logging.getLogger(__name__)

COUNT = "redaction.server_count"
KINDS = "redaction.server_kinds"
REASON = "redaction.dropped_reason"

MODE_METADATA_ONLY = "mode_metadata_only"
MODE_DISABLED = "mode_disabled"
SCAN_FAILED = "scan_failed"
TOO_LARGE = "too_large"
# One reason is stored. When several apply, the most informative wins: a failure says more than
# the mode, which the project's settings already state.
_PRECEDENCE = (SCAN_FAILED, TOO_LARGE, MODE_DISABLED, MODE_METADATA_ONLY)

DROPPED = "[dropped]"  # stands in for a required attribute that could not be kept
MAX_EVENT_SCAN_CHARS = 2 * 1024 * 1024  # all strings of one event (the event itself is <= 256 KiB)
MAX_PAYLOAD_NODES = 5000
MAX_LABEL_CHARS = 1024  # an artifact name is at most 128 characters; a little room for markers

# The SDK's default deny list (blackbox.redaction.DEFAULT_DENY_KEYS): a string under such a key of
# a structured payload is replaced whatever it looks like.
DENY_KEYS: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key", "authorization",
    "cookie", "credential", "private_key", "access_key",
)  # fmt: skip
_DISABLED_DROPS_EXACT = frozenset({"error.message"})
_DISABLED_DROPS_PREFIX = "metadata."


class RedactionError(Exception):
    """An event could not be redacted into a valid event. It must not be stored as received."""


@dataclass
class _Run:
    """Scratch state for one event."""

    budget: ScanBudget
    count: int = 0
    kinds: set[str] = field(default_factory=set)
    reasons: set[str] = field(default_factory=set)

    def found(self, count: int, kinds: tuple[str, ...]) -> None:
        self.count += count
        self.kinds.update(kinds)


def _norm(key: str) -> str:
    return key.lower().replace("-", "_")


def _denied(key: str) -> bool:
    k = _norm(key)
    return any(d in k for d in DENY_KEYS)


class ServerRedactor:
    def __init__(
        self,
        scanner: SecretScanner | None = None,
        *,
        event_max_chars: int = MAX_EVENT_SCAN_CHARS,
        event_max_seconds: float = DEFAULT_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._scanner = scanner or SecretScanner()
        self._max_chars = event_max_chars
        self._max_seconds = event_max_seconds
        self._clock = clock

    # ------------------------------------------------------------------ events

    def apply(self, event: Event, mode: CaptureMode) -> Event:
        """The event as it will be stored. Returns `event` itself when nothing changes."""
        run = _Run(ScanBudget(self._max_chars, self._max_seconds, clock=self._clock))
        required = lookup(event.event_type)
        required_keys = required.required_attributes if required else frozenset()

        attributes = {
            k: v for k, v in event.attributes.items() if k not in SERVER_REDACTION_ATTRIBUTES
        }
        payload = event.payload
        payload_ref = event.payload_ref
        tags = list(event.tags)

        if mode is not CaptureMode.FULL:
            reason = MODE_DISABLED if mode is CaptureMode.DISABLED else MODE_METADATA_ONLY
            if payload is not None or payload_ref is not None:
                run.reasons.add(reason)
            payload = payload_ref = None
            for key in PAYLOAD_REFERENCE_ATTRIBUTES & attributes.keys():
                del attributes[key]
                run.reasons.add(reason)
            if mode is CaptureMode.DISABLED:
                for key in [
                    k
                    for k in attributes
                    if k in _DISABLED_DROPS_EXACT or k.startswith(_DISABLED_DROPS_PREFIX)
                ]:
                    del attributes[key]
                    run.reasons.add(MODE_DISABLED)

        # Metadata first: when the budget runs out, the payload (the bulk) is what gets dropped.
        for key in sorted(attributes):
            value = attributes[key]
            if isinstance(value, str):
                scanned = self._text(value, run)
                if scanned is None:
                    self._drop(attributes, key, required_keys)
                else:
                    attributes[key] = _fit(scanned, limits.MAX_ATTRIBUTE_STRING_LENGTH)
            elif isinstance(value, list):
                items = [self._text(i, run) if isinstance(i, str) else i for i in value]
                if any(i is None for i in items):
                    self._drop(attributes, key, required_keys)
                else:
                    attributes[key] = [
                        _fit(i, limits.MAX_ATTRIBUTE_STRING_LENGTH) if isinstance(i, str) else i
                        for i in items
                    ]
        for index, tag in enumerate(tags):
            scanned = self._text(tag, run)
            tags[index] = _fit(scanned, limits.MAX_TAG_LENGTH) if scanned is not None else DROPPED
        if payload is not None:
            payload = self._payload(payload, run)

        if run.count:
            attributes[COUNT] = run.count
            attributes[KINDS] = sorted(run.kinds)
        for reason in _PRECEDENCE:
            if reason in run.reasons:
                attributes[REASON] = reason
                break
        _make_room(attributes, required_keys)

        if (
            attributes == event.attributes
            and payload == event.payload
            and payload_ref == event.payload_ref
            and tags == event.tags
        ):
            return event
        try:
            return Event.model_validate(
                {
                    **event.__dict__,
                    "attributes": attributes,
                    "payload": payload,
                    "payload_ref": payload_ref,
                    "tags": tags,
                }
            )
        except Exception:
            # Unreachable by construction (sizes are fitted above); never store it as received.
            logger.error("redacted event failed validation", exc_info=False)
            raise RedactionError("redacted event is invalid") from None

    def _text(self, value: str, run: _Run) -> str | None:
        """The scanned string; None when it must be dropped (the reason is recorded)."""
        try:
            result = self._scanner.scan_text(value, run.budget)
        except ScanTooLarge:
            run.reasons.add(TOO_LARGE)
            return None
        except ScanFailed:
            run.reasons.add(SCAN_FAILED)
            return None
        except Exception:
            run.reasons.add(SCAN_FAILED)
            return None
        run.found(result.count, result.kinds)
        return result.text

    @staticmethod
    def _drop(attributes: dict[str, Any], key: str, required: frozenset[str]) -> None:
        # A required attribute stays (as a marker): the stored event must still be valid.
        if key in required:
            attributes[key] = DROPPED
        else:
            del attributes[key]

    def _payload(self, payload: dict[str, Any], run: _Run) -> dict[str, Any] | None:
        """The scanned inline payload, or None to drop all of it."""
        budget = [MAX_PAYLOAD_NODES]
        try:
            cleaned = self._value(payload, "", run, budget)
        except _Drop as reason:
            run.reasons.add(str(reason))
            return None
        assert isinstance(cleaned, dict)
        if _json_size(cleaned) > limits.MAX_INLINE_PAYLOAD_BYTES:
            run.reasons.add(TOO_LARGE)  # redaction markers can be longer than what they replace
            return None
        return cleaned

    def _value(self, value: Any, key: str, run: _Run, budget: list[int]) -> Any:
        budget[0] -= 1
        if budget[0] < 0:
            raise _Drop(TOO_LARGE)
        if key and _denied(key) and isinstance(value, str | dict | list):
            # Like the SDK: a text, object or list under a key named password/token/... goes whole.
            label = f"[REDACTED:{_norm(key)}]"
            if isinstance(value, str) and value.startswith("[REDACTED"):
                return value  # already redacted upstream: not a finding
            run.found(1, (DENIED_KEY_KIND,))
            return label
        if isinstance(value, str):
            scanned = self._text(value, run)
            if scanned is None:
                raise _Drop(TOO_LARGE if TOO_LARGE in run.reasons else SCAN_FAILED)
            return scanned
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                clean_key = self._text(k, run)
                if clean_key is None:
                    raise _Drop(TOO_LARGE if TOO_LARGE in run.reasons else SCAN_FAILED)
                out[clean_key] = self._value(v, k, run, budget)
            return out
        if isinstance(value, list):
            return [self._value(v, key, run, budget) for v in value]
        return value  # numbers, booleans, null cannot carry a credential

    # ------------------------------------------------------------- free-form values

    def scrub_json(
        self, value: Any, *, max_chars: int = 128 * 1024, max_seconds: float = DEFAULT_MAX_SECONDS
    ) -> "Scrubbed | None":
        """A JSON value (or a string) with secret shapes and denied-key values replaced, for text
        the server stores outside an event (an approval's arguments, a decision comment). Same
        scanner, budget and fail-closed rule as event redaction: `None` when it cannot be scanned
        (error, too large, out of time), in which case the caller stores nothing of it."""
        run = _Run(ScanBudget(max_chars, max_seconds, clock=self._clock))
        try:
            cleaned = self._value(value, "", run, [MAX_PAYLOAD_NODES])
        except _Drop:
            return None
        except Exception:
            return None
        return Scrubbed(cleaned, run.count, tuple(sorted(run.kinds)))

    # --------------------------------------------------------------- artifacts

    def label(self, text: str, *, max_seconds: float = DEFAULT_MAX_SECONDS) -> str | None:
        """A short free-text label (an artifact name) with secret shapes replaced; None when it
        cannot be scanned, in which case the caller stores no label. Deterministic, so a
        redelivered upload carries the same label."""
        budget = ScanBudget(MAX_LABEL_CHARS, max_seconds, clock=self._clock)
        try:
            return self._scanner.scan_text(text, budget).text
        except Exception:
            return None

    def artifact_body(
        self, data: bytes, mode: CaptureMode, *, max_bytes: int, max_seconds: float
    ) -> "ArtifactDecision":
        """What to store for an uploaded artifact body under the project's capture mode."""
        if mode is CaptureMode.METADATA_ONLY:
            return ArtifactDecision.dropped(MODE_METADATA_ONLY)
        if mode is CaptureMode.DISABLED:
            return ArtifactDecision.dropped(MODE_DISABLED)
        if len(data) > max_bytes:
            return ArtifactDecision.dropped(TOO_LARGE)
        budget = ScanBudget(max_bytes, max_seconds, clock=self._clock)
        try:
            result = self._scanner.scan_bytes(data, budget)
        except ScanTooLarge:
            return ArtifactDecision.dropped(TOO_LARGE)
        except ScanFailed:
            return ArtifactDecision.dropped(SCAN_FAILED)
        except Exception:
            return ArtifactDecision.dropped(SCAN_FAILED)
        return ArtifactDecision.kept(result)


@dataclass(frozen=True)
class Scrubbed:
    """A scrubbed value and what was found: a count and kind names, never a secret."""

    value: Any
    count: int
    kinds: tuple[str, ...]


class _Drop(Exception):
    """Abandon the whole payload; the message is the reason."""


def placeholder_body(reason: str) -> bytes:
    """What a reader sees where a body was not stored: a plain sentence, never a secret."""
    return f"[Agent BlackBox: artifact body not stored ({reason})]\n".encode()


@dataclass(frozen=True)
class ArtifactDecision:
    body: bytes
    count: int = 0
    kinds: tuple[str, ...] = ()
    dropped_reason: str | None = None

    @staticmethod
    def dropped(reason: str) -> "ArtifactDecision":
        return ArtifactDecision(placeholder_body(reason), dropped_reason=reason)

    @staticmethod
    def kept(result: BytesScanResult) -> "ArtifactDecision":
        return ArtifactDecision(result.data, result.count, result.kinds)

    def record(self) -> dict[str, Any]:
        """The `artifacts.metadata["redaction"]` entry: counts and kind names, never values."""
        entry: dict[str, Any] = {"server_count": self.count, "server_kinds": list(self.kinds)}
        if self.dropped_reason:
            entry["dropped_reason"] = self.dropped_reason
        return entry


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _json_size(value: Mapping[str, Any]) -> int:
    import json

    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _make_room(attributes: dict[str, Any], required: frozenset[str]) -> None:
    """At most 64 attributes: if the markers do not fit, drop optional ones, last key first."""
    excess = len(attributes) - limits.MAX_ATTRIBUTES
    if excess <= 0:
        return
    keep = required | SERVER_REDACTION_ATTRIBUTES
    for key in sorted((k for k in attributes if k not in keep), reverse=True)[:excess]:
        del attributes[key]
