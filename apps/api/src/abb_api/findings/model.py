"""The finding contract (spec §81.2), detector context and the rules every finding obeys.

Evidence is metadata only (ADR-080): each finding type has an allowlist of evidence keys, every
client-controlled string is truncated, every list is bounded and the serialised evidence is kept
under 15 KiB (the database CHECK at 16 KiB is only a backstop). A violation is a detector bug and
raises `EvidenceError`, which `run_all` turns into a fixed-message `DetectorError`.
"""

import hashlib
import json
import math
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event
from abb_event_schema.spans import Span

MAX_PER_DETECTOR = 50
MAX_PER_RUN = 200
MAX_IDS = 50  # event ids one finding may cite (evidence_event_ids)
MAX_LABEL = 128  # characters of any client-controlled string in evidence
MAX_PATTERN_TOKENS = 20
MAX_EVIDENCE_BYTES = 15 * 1024
MAX_RUN_EVIDENCE_BYTES = 1024 * 1024  # all findings of one run; lowest severity dropped first

# Namespace of finding ids (UUIDv5 over workspace, run and fingerprint): fixed forever so an id
# survives every rewrite and rebuild.
FINDING_NAMESPACE = uuid.UUID("5d1c0e2a-7b64-5f0e-9a3c-0b1ab10c4b0b")


class Severity(StrEnum):
    """`info`, `warning` and `critical` are the reliability scale; the security detectors added
    `medium` and `high` (ADR-131): info < warning < medium < high < critical."""

    INFO = "info"
    WARNING = "warning"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Kind(StrEnum):
    DETERMINISTIC = "deterministic"
    PROBABLE = "probable"


class EvidenceError(ValueError):
    """Evidence broke the contract (a key outside the allowlist, an unknown event id, a size)."""


class DetectorError(Exception):
    """A detector raised. The message is fixed text: neither `outbox_jobs.last_error` nor a
    dead-letter traceback may carry an attribute value (ADR-080). Raise it `from None`."""

    def __init__(self, detector: str, event_count: int) -> None:
        super().__init__(f"detector {detector} failed over {event_count} events")
        self.detector = detector
        self.event_count = event_count


@dataclass(frozen=True)
class DetectorConfig:
    """Thresholds. Deployment-wide settings (`ABB_FINDINGS_*`) override the defaults; every
    threshold a finding used is recorded in its `evidence.thresholds` (ADR-081)."""

    loop_min_repetitions: int = 3
    loop_min_repetitions_single: int = 8
    loop_min_tokens: int = 6
    loop_max_pattern_length: int = 8
    identical_min_count: int = 3
    retry_storm_min: int = 5
    retry_burst_min: int = 3
    retry_burst_window_seconds: int = 60
    context_growth_min_calls: int = 5
    context_growth_min_delta_tokens: int = 4_000
    context_growth_factor: float = 2.0
    context_growth_warning_tokens: int = 100_000
    cascade_min_failures: int = 3
    cascade_window_calls: int = 10
    cost_spike_min_baseline: int = 20
    cost_spike_window_days: int = 30

    def thresholds(self, *names: str) -> dict[str, int | float]:
        return {name: getattr(self, name) for name in names}


THRESHOLD_KEYS = frozenset(f.name for f in fields(DetectorConfig))


@dataclass(frozen=True)
class RunFacts:
    """What the detectors and health know about the run. Status, completion and counters are
    folded from the loaded events (one snapshot with them); cost comes from the run row."""

    status: RunStatus
    terminal_event_id: str | None
    started_at: datetime
    event_count: int
    error_count: int
    retry_count: int
    retries_unattributed: int
    tool_spans_finished: int
    tool_spans_ok: int
    ordering_mode: str
    agent_slug: str
    cost_usd: Decimal = Decimal(0)
    retry_cost_usd: Decimal = Decimal(0)


@dataclass(frozen=True)
class CostBaseline:
    n: int
    median_usd: Decimal
    p95_usd: Decimal
    window_days: int
    newest_started_at: datetime
    agent_slug: str


@dataclass(frozen=True)
class SecurityInputs:
    """What the security detectors need beyond the events (ADR-131): the project's settings and
    the redaction records of the run's artifacts. All of it is configuration or metadata; none of
    it is captured content."""

    muted: frozenset[str] = frozenset()  # detector ids (`sec.*`) that produce no findings
    destination_allowlist: tuple[str, ...] = ()  # host patterns; empty = report no destinations
    # Artifact public id (`art_...`) -> credential kinds its server-side redaction record names.
    artifact_kinds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectorContext:
    events: tuple[Event, ...]  # canonical order, once
    spans: Mapping[str, Span]
    facts: RunFacts
    config: DetectorConfig
    baseline: CostBaseline | None = None
    event_ids: frozenset[str] = field(default_factory=frozenset)
    position: Mapping[str, int] = field(default_factory=dict)
    security: "SecurityInputs" = field(default_factory=lambda: SecurityInputs())


@dataclass(frozen=True)
class Finding:
    detector: str
    detector_version: str  # name@x.y.z (§81.2)
    finding_type: str
    kind: Kind
    severity: Severity
    confidence: float
    start_event_id: str | None
    end_event_id: str | None
    evidence_event_ids: tuple[str, ...]
    fingerprint: str
    evidence: dict[str, Any]


# --------------------------------------------------------------------------- evidence allowlists

SECURITY_PREFIX = "sec."
SECURITY_FINDING_TYPES = (
    "secret_file_read",
    "credential_in_output",
    "destructive_command",
    "risky_git",
    "privileged_db",
    "unexpected_destination",
)

EVIDENCE_KEYS: dict[str, frozenset[str]] = {
    "possible_loop": frozenset(
        {
            "agent_id",
            "pattern",
            "repetitions",
            "event_ids",
            "start_event_id",
            "end_event_id",
            "duration_ms",
            "thresholds",
        }
    ),
    "identical_request": frozenset(
        {"agent_id", "event_type", "tool_name", "count", "event_ids", "thresholds"}
    ),
    "retry_storm": frozenset(
        {
            "agent_id",
            "retries",
            "retries_unattributed",
            "burst",
            "by_scope",
            "rate_limit_hits",
            "window_seconds",
            "thresholds",
        }
    ),
    "context_growth": frozenset(
        {
            "agent_id",
            "calls",
            "first_tokens",
            "last_tokens",
            "growth_factor",
            "mean_delta_tokens",
            "superlinear",
            "series",
            "start_event_id",
            "end_event_id",
            "thresholds",
        }
    ),
    "cost_spike": frozenset({"cost_usd", "baseline", "thresholds"}),
    "tool_failure_cascade": frozenset(
        {
            "agent_id",
            "failures",
            "calls_in_window",
            "retries_between",
            "alternates",
            "start_event_id",
            "end_event_id",
            "thresholds",
        }
    ),
    "failure_chain": frozenset(
        {
            "category",
            "terminal_event_id",
            "root_event_id",
            "chain",
            "span_lineage",
            "retries_caused",
            "retries_unattributed",
            "mutations",
        }
    ),
    "probable_cause": frozenset({"cause", "event_ids", "tool_name", "model"}),
    "reported_loop": frozenset({"pattern", "repetitions", "event_id"}),
    # Security (ADR-131): fixed rule words, counts, event/artifact ids and credential kind names
    # from a closed vocabulary. Never a path, command, host or value.
    **{
        name: frozenset({"rule", "rules", "count", "event_ids", "artifact_ids", "kinds"})
        for name in SECURITY_FINDING_TYPES
    },
}

# Keys of the objects nested inside evidence lists or dicts (also an allowlist).
NESTED_KEYS: dict[str, frozenset[str]] = {
    "by_scope": frozenset({"span_id", "count", "first_event_id", "last_event_id"}),
    "series": frozenset({"event_id", "input_tokens"}),
    "failures": frozenset(
        {"event_id", "event_type", "tool_name", "error_type", "exit_code", "status_code"}
    ),
    "chain": frozenset({"event_id", "event_type", "role", "span_id"}),
    "baseline": frozenset(
        {"n", "median_usd", "p95_usd", "window_days", "newest_started_at", "agent_slug"}
    ),
    "thresholds": THRESHOLD_KEYS,
}

# Bounds of lists inside evidence (default MAX_IDS). `event_ids` leaves room for start and end.
LIST_LIMITS: dict[str, int] = {
    "pattern": MAX_PATTERN_TOKENS,
    "event_ids": MAX_IDS - 2,
    "by_scope": 20,
    "failures": 20,
    "alternates": 10,
    "span_lineage": 20,
    "series": MAX_IDS,
    "chain": MAX_IDS,
}

_ID_KEYS = frozenset(
    {
        "event_id",
        "first_event_id",
        "last_event_id",
        "start_event_id",
        "end_event_id",
        "terminal_event_id",
        "root_event_id",
    }
)


def label(value: str, limit: int = MAX_LABEL) -> str:
    """A client-controlled string, bounded."""
    return value if len(value) <= limit else value[:limit]


def _clean_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceError("evidence number is not finite")
        return value
    if isinstance(value, str):
        return label(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    raise EvidenceError("evidence value has an unsupported type")


def _clean_object(key: str, value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = NESTED_KEYS.get(key)
    if allowed is None:
        raise EvidenceError("evidence object not allowed here")
    out: dict[str, Any] = {}
    for inner, item in value.items():
        if inner not in allowed:
            raise EvidenceError("evidence key is not allowed")
        out[inner] = _clean_scalar(item)
    return out


def _clean_value(key: str, value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        limit = LIST_LIMITS.get(key, MAX_IDS)
        return [
            _clean_object(key, item) if isinstance(item, Mapping) else _clean_scalar(item)
            for item in list(value)[:limit]
        ]
    if isinstance(value, Mapping):
        return _clean_object(key, value)
    return _clean_scalar(value)


def _walk_ids(evidence: Mapping[str, Any]) -> list[str]:
    found: list[str] = []

    def visit(key: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for inner_key, item in value.items():
                visit(inner_key, item)
        elif isinstance(value, list):
            for item in value:
                if key == "event_ids" and isinstance(item, str):
                    found.append(item)
                elif isinstance(item, Mapping):
                    visit(key, item)
        elif key in _ID_KEYS and isinstance(value, str):
            found.append(value)

    for key, value in evidence.items():
        visit(key, value)
    return found


def _size(evidence: Mapping[str, Any]) -> int:
    return len(json.dumps(evidence, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def validate_evidence(
    finding_type: str,
    evidence: Mapping[str, Any],
    start_event_id: str | None = None,
    end_event_id: str | None = None,
    known_event_ids: frozenset[str] | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return (clean evidence, every event id it cites). Raises `EvidenceError`.

    Keys outside the finding type's allowlist are rejected (never silently dropped: that would
    hide a detector that copies content). Strings are truncated to 128 characters, lists to their
    bounds; evidence that still cites more than 50 events or exceeds 15 KiB raises (it cannot under
    the current limits, which is what keeps a hostile run away from the database CHECK).
    """
    allowed = EVIDENCE_KEYS.get(finding_type)
    if allowed is None:
        raise EvidenceError("unknown finding type")
    clean: dict[str, Any] = {}
    for key, value in evidence.items():
        if key not in allowed:
            raise EvidenceError("evidence key is not allowed")
        clean[key] = _clean_value(key, value)

    def ids() -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for item in (start_event_id, end_event_id, *_walk_ids(clean)):
            if item is not None:
                seen.setdefault(item, None)
        return tuple(seen)

    # The bounds hold by construction (`LIST_LIMITS`, `MAX_LABEL`, the key allowlist); these checks
    # are the net under a future limit that stops adding up. Trimming here would silently cut the
    # evidence a finding is cited by, so a breach fails the job loudly (as a `DetectorError`).
    cited = ids()
    if len(cited) > MAX_IDS:
        raise EvidenceError("evidence cites too many events")
    if _size(clean) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("evidence is too large")
    if known_event_ids is not None and any(i not in known_event_ids for i in cited):
        raise EvidenceError("evidence cites an event that is not in the run")
    return clean, cited


def fingerprint(detector: str, finding_type: str, start_event_id: str | None, key: str) -> str:
    """Stable identity across rebuilds (ADR-080): sha256 over detector, type, start and key."""
    material = "\x1f".join((detector, finding_type, start_event_id or "", key))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def finding_uuid(workspace_id: uuid.UUID, run_id: uuid.UUID, fingerprint_hex: str) -> uuid.UUID:
    """The finding's id, derived so it is stable across rewrites (public form: `fnd_...`)."""
    return uuid.uuid5(FINDING_NAMESPACE, f"{workspace_id}:{run_id}:{fingerprint_hex}")


class Detector(ABC):
    """One rule set. Pure: events in, findings out; never raises on hostile attribute values."""

    name: str
    version: str

    @property
    def detector_version(self) -> str:
        return f"{self.name}@{self.version}"

    @abstractmethod
    def detect(self, ctx: DetectorContext) -> list[Finding]: ...

    def make(
        self,
        ctx: DetectorContext,
        *,
        finding_type: str,
        kind: Kind,
        severity: Severity,
        confidence: float,
        start_event_id: str | None,
        end_event_id: str | None,
        key: str,
        evidence: Mapping[str, Any],
    ) -> Finding:
        clean, cited = validate_evidence(
            finding_type, evidence, start_event_id, end_event_id, ctx.event_ids
        )
        return Finding(
            detector=self.name,
            detector_version=self.detector_version,
            finding_type=finding_type,
            kind=kind,
            severity=severity,
            confidence=round(min(1.0, max(0.0, confidence)), 3),
            start_event_id=start_event_id,
            end_event_id=end_event_id,
            evidence_event_ids=cited,
            fingerprint=fingerprint(self.name, finding_type, start_event_id, key),
            evidence=clean,
        )
