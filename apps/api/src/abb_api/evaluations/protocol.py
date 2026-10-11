"""The evaluator contract (spec §85.1): a pure function of a run snapshot, under a budget.

Evaluators run synchronously in a worker thread (ADR-090). Python cannot kill a thread, so the
contract is cooperative: an implementation calls `budget.check()` at least once per 1,000 events
and once per glob pattern, and the handler stops *waiting* after the timeout whether or not it
did. Results are bounded and validated before they are stored (`clean_result`).
"""

import json
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol

from abb_event_schema.event import Event
from abb_event_schema.spans import Span
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_EXPLANATION_CHARS = 4_000
MAX_EXPLANATION_BYTES = 16_384
MAX_LABEL_CHARS = 64
MAX_METRIC_STRING = 256
MAX_METRICS = 32
MAX_EVIDENCE_IDS = 100
MAX_METADATA_BYTES = 8_192
EVENTS_PER_CHECK = 1_000


class EvaluatorError(Exception):
    """A built-in evaluator cannot produce a result. The message is fixed, templated text: the
    only exception text that is ever stored (`evaluations.error`). Raise it `from None`."""


class BudgetExceeded(Exception):
    """The evaluator ran past its deadline (cooperative cancellation)."""

    def __init__(self) -> None:
        super().__init__("evaluator budget exhausted")


class InvalidResult(Exception):
    """A result broke the contract. Carries no value from the result (hostile input)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class Budget:
    """A wall-clock deadline the evaluator checks cooperatively."""

    def __init__(self, seconds: float, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + seconds

    def expired(self) -> bool:
        return bool(self._clock() >= self._deadline)

    def check(self) -> None:
        if self.expired():
            raise BudgetExceeded


@dataclass(frozen=True)
class RunSnapshot:
    """Everything an evaluator may read: the run row's facts and its events, in canonical order."""

    run_id: str
    status: str
    started_at: datetime
    duration_ms: float | None
    agent_slug: str | None
    ordering_mode: str
    summary: Mapping[str, Any]
    events: tuple[Event, ...]
    spans: Mapping[str, Span] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationResult:
    """A result as spec §85.1 describes it. `metadata` is the implementation's own bounded
    record (judge model, tokens, ...); built-ins leave it empty."""

    score: float | None
    label: str
    explanation: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    evidence_event_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Evaluator(Protocol):
    name: ClassVar[str]
    version: ClassVar[int]
    kind: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    metric_keys: ClassVar[frozenset[str]]

    def evaluate(
        self, snapshot: RunSnapshot, config: BaseModel, budget: Budget
    ) -> EvaluationResult: ...


def _clean_metric(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidResult("metric is not finite")
        return value
    if isinstance(value, str) and len(value) <= MAX_METRIC_STRING and "\x00" not in value:
        return value
    raise InvalidResult("metric value has an unsupported type or size")


def clean_result(
    result: object, metric_keys: frozenset[str], event_ids: frozenset[str]
) -> EvaluationResult:
    """Validate what an evaluator returned. Raises `InvalidResult` with a fixed reason.

    Metric keys must come from the implementation's allowlist (a free-form key could carry
    content to a reader without `payload.read`); evidence ids that are not events of the run are
    dropped and logged (never stored); everything else out of bounds is an error, not a truncation.
    """
    if not isinstance(result, EvaluationResult):
        raise InvalidResult("result has the wrong type")
    score = result.score
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise InvalidResult("score is not a number")
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise InvalidResult("score is outside [0, 1]")
        score = float(score)
    if not isinstance(result.label, str) or not 0 < len(result.label) <= MAX_LABEL_CHARS:
        raise InvalidResult("label is missing or too long")
    if "\x00" in result.label:  # PostgreSQL text cannot hold NUL: it would fail the write
        raise InvalidResult("label contains a NUL character")
    explanation = result.explanation
    if (
        not isinstance(explanation, str)
        or len(explanation) > MAX_EXPLANATION_CHARS
        or len(explanation.encode("utf-8")) > MAX_EXPLANATION_BYTES
        or "\x00" in explanation
    ):
        raise InvalidResult("explanation is not text, too long or contains NUL")
    if not isinstance(result.metrics, Mapping) or len(result.metrics) > MAX_METRICS:
        raise InvalidResult("metrics are not an object or too many")
    metrics: dict[str, Any] = {}
    for key, value in result.metrics.items():
        if key not in metric_keys:
            raise InvalidResult("metric key is not in the implementation's allowlist")
        metrics[key] = _clean_metric(value)
    if not isinstance(result.evidence_event_ids, (tuple, list)):
        raise InvalidResult("evidence is not a list")
    cited = [i for i in result.evidence_event_ids if isinstance(i, str)]
    kept = tuple(i for i in dict.fromkeys(cited) if i in event_ids)
    if len(kept) != len(set(cited)):
        logger.warning("evaluator cited events outside the run", extra={"dropped": True})
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    try:
        too_big = len(json.dumps(metadata, default=str).encode("utf-8")) > MAX_METADATA_BYTES
    except (TypeError, ValueError):
        raise InvalidResult("metadata is not JSON") from None
    if too_big:
        raise InvalidResult("metadata is too large")
    if "\\u0000" in json.dumps(metadata, default=str):
        raise InvalidResult("metadata contains a NUL character")
    return EvaluationResult(
        score=score,
        label=result.label,
        explanation=explanation,
        metrics=metrics,
        evidence_event_ids=kept[:MAX_EVIDENCE_IDS],
        metadata=dict(metadata),
    )
