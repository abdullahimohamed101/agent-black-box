"""The built-in evaluators (spec §85.2): deterministic checks and metric thresholds.

Every implementation is pure and synchronous, builds its explanation from a template with numbers
only (never an attribute value) and returns metrics whose keys come from its own allowlist, so a
result is identical for two runs that differ only in captured content (ADR-090, KI-078). Path
globs are user configuration and are bounded: at most 32 patterns of 256 characters in total, each
compiled once with `fnmatch.translate`; `**` means `*`, and the budget is checked per pattern and
every few hundred paths.
"""

import fnmatch
import math
import posixpath
import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, ClassVar

from abb_event_schema.event import Event
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from abb_api.evaluations.protocol import (
    EVENTS_PER_CHECK,
    MAX_EVIDENCE_IDS,
    Budget,
    EvaluationResult,
    EvaluatorError,
    RunSnapshot,
)

MAX_PATTERNS = 32
MAX_PATTERN_CHARS = 256
MAX_PATH_CHARS = 4_096
PATHS_PER_CHECK = 256
DEFAULT_LABELS = ("pass", "fail", "unsure")
FILE_EVENTS = frozenset({"file.created", "file.modified", "file.deleted"})
WRITE_EVENTS = frozenset({"file.created", "file.modified"})

# Configuration fields that name paths or hold a rubric: Class 2 content, withheld from actors
# without `payload.read` (decision 6).
WITHHELD_CONFIG_FIELDS = frozenset({"allowed_globs", "forbidden_globs", "required_paths", "rubric"})

Pattern = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_PATTERN_CHARS, pattern=r"^[^\x00-\x1f\x7f]+$"),
]
Label = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
]


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoConfig(StrictConfig):
    pass


class FilesTouchedConfig(StrictConfig):
    allowed_globs: list[Pattern] = Field(default_factory=list)
    forbidden_globs: list[Pattern] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bounded(self) -> "FilesTouchedConfig":
        total = len(self.allowed_globs) + len(self.forbidden_globs)
        if total == 0:
            raise ValueError("give at least one allowed or forbidden glob")
        if total > MAX_PATTERNS:
            raise ValueError(f"at most {MAX_PATTERNS} patterns in total")
        return self


class RequiredPathsConfig(StrictConfig):
    required_paths: list[Pattern] = Field(min_length=1, max_length=MAX_PATTERNS)


class CostUnderConfig(StrictConfig):
    max_usd: float = Field(ge=0, le=1e9, allow_inf_nan=False)


class LatencyUnderConfig(StrictConfig):
    max_ms: float = Field(ge=0, le=1e12, allow_inf_nan=False)


class ErrorRateConfig(StrictConfig):
    max_rate: float = Field(ge=0, le=1, allow_inf_nan=False)


class CountUnderConfig(StrictConfig):
    max: int = Field(ge=0, le=1_000_000)


class HumanConfig(StrictConfig):
    allowed_labels: list[Label] = Field(
        default_factory=lambda: list(DEFAULT_LABELS), min_length=1, max_length=10
    )


# --------------------------------------------------------------------------------- helpers


def normalise_path(path: str) -> str:
    """A relative-looking POSIX path: backslashes folded, `.` and `..` collapsed."""
    return posixpath.normpath(path.replace("\\", "/"))


def compile_globs(patterns: Iterable[str], budget: Budget) -> list[re.Pattern[str]]:
    compiled = []
    for pattern in patterns:
        budget.check()
        compiled.append(re.compile(fnmatch.translate(re.sub(r"\*+", "*", pattern))))
    return compiled


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(source: Mapping[str, Any], key: str) -> float | None:
    value = source.get(key)
    return float(value) if isinstance(value, (int, float)) and _is_number(value) else None


def _int_attr(event: Event, key: str) -> int | None:
    value = event.attributes.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _ids(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(list(ids)[:MAX_EVIDENCE_IDS])


def _verdict(passed: bool) -> tuple[float, str]:
    return (1.0, "pass") if passed else (0.0, "fail")


def _scan(events: tuple[Event, ...], budget: Budget) -> Iterable[Event]:
    for index, event in enumerate(events):
        if index % EVENTS_PER_CHECK == 0:
            budget.check()
        yield event


# --------------------------------------------------------------------------- deterministic


class RunSucceeded:
    name: ClassVar = "run_succeeded"
    version: ClassVar = 1
    kind: ClassVar = "deterministic"
    config_model: ClassVar = NoConfig
    metric_keys: ClassVar = frozenset({"status"})

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        score, label = _verdict(snapshot.status == "SUCCESS")
        return EvaluationResult(
            score,
            label,
            f"The run ended with status {snapshot.status}.",
            {"status": snapshot.status},
        )


class TestsPassed:
    __test__ = False  # not a pytest class
    name: ClassVar = "tests_passed"
    version: ClassVar = 1
    kind: ClassVar = "deterministic"
    config_model: ClassVar = NoConfig
    metric_keys: ClassVar = frozenset({"tests_total", "tests_passed", "tests_failed"})

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        last: Event | None = None
        for event in _scan(snapshot.events, budget):
            if event.event_type == "shell.command.completed" and isinstance(
                event.attributes.get("test.framework"), str
            ):
                last = event
        failed = _int_attr(last, "test.failed") if last is not None else None
        if last is None or failed is None:
            return EvaluationResult(
                None, "unknown", "No test run with a failure count was recorded."
            )
        total, passed = _int_attr(last, "test.total"), _int_attr(last, "test.passed")
        metrics: dict[str, Any] = {"tests_failed": failed}
        if total is not None:
            metrics["tests_total"] = total
        if passed is not None:
            metrics["tests_passed"] = passed
        score, label = _verdict(failed == 0)
        return EvaluationResult(
            score,
            label,
            f"The last test run had {failed} failing tests.",
            metrics,
            (last.event_id,),
        )


class FilesTouchedOnly:
    name: ClassVar = "files_touched_only"
    version: ClassVar = 1
    kind: ClassVar = "deterministic"
    config_model: ClassVar = FilesTouchedConfig
    metric_keys: ClassVar = frozenset({"files_checked", "violations"})

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        allowed = compile_globs(config.allowed_globs, budget)
        forbidden = compile_globs(config.forbidden_globs, budget)
        checked = 0
        violations: list[str] = []
        for event in _scan(snapshot.events, budget):
            if event.event_type not in FILE_EVENTS:
                continue
            raw = event.attributes.get("file.path")
            if not isinstance(raw, str):
                continue
            if checked % PATHS_PER_CHECK == 0:
                budget.check()
            checked += 1
            path = normalise_path(raw) if len(raw) <= MAX_PATH_CHARS else None
            outside = path is None or (bool(allowed) and not any(rx.match(path) for rx in allowed))
            if outside or (path is not None and any(rx.match(path) for rx in forbidden)):
                violations.append(event.event_id)
        score, label = _verdict(not violations)
        return EvaluationResult(
            score,
            label,
            f"{len(violations)} of {checked} file changes broke the path rules.",
            {"files_checked": checked, "violations": len(violations)},
            _ids(violations),
        )


class NoPolicyBlocks:
    name: ClassVar = "no_policy_blocks"
    version: ClassVar = 1
    kind: ClassVar = "deterministic"
    config_model: ClassVar = NoConfig
    metric_keys: ClassVar = frozenset({"blocked"})

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        blocked = [
            e.event_id
            for e in _scan(snapshot.events, budget)
            if e.event_type == "policy.action.blocked"
        ]
        score, label = _verdict(not blocked)
        return EvaluationResult(
            score,
            label,
            f"{len(blocked)} actions were blocked by policy.",
            {"blocked": len(blocked)},
            _ids(blocked),
        )


class ExpectedFilesChanged:
    name: ClassVar = "expected_files_changed"
    version: ClassVar = 1
    kind: ClassVar = "deterministic"
    config_model: ClassVar = RequiredPathsConfig
    metric_keys: ClassVar = frozenset({"required", "missing"})

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        required = {normalise_path(p) for p in config.required_paths}
        touched: dict[str, str] = {}
        for event in _scan(snapshot.events, budget):
            if event.event_type in WRITE_EVENTS:
                raw = event.attributes.get("file.path")
                if isinstance(raw, str) and len(raw) <= MAX_PATH_CHARS:
                    touched.setdefault(normalise_path(raw), event.event_id)
        missing = len(required - touched.keys())
        score, label = _verdict(missing == 0)
        return EvaluationResult(
            score,
            label,
            f"{missing} of {len(required)} required files were not changed.",
            {"required": len(required), "missing": missing},
            _ids(touched[p] for p in sorted(required & touched.keys())),
        )


# -------------------------------------------------------------------------------- metrics


class _Threshold:
    """value <= limit, from a number in the run summary; unknown when the run has no figure."""

    kind: ClassVar = "metric"
    metric_keys: ClassVar = frozenset({"value", "threshold"})
    unit: ClassVar[str]

    def _figure(self, snapshot: RunSnapshot) -> float | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _limit(self, config: Any) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        value = self._figure(snapshot)
        limit = self._limit(config)
        if value is None:
            return EvaluationResult(None, "unknown", "The run has no figure for this metric.")
        score, label = _verdict(value <= limit)
        return EvaluationResult(
            score,
            label,
            f"{self.unit}: {_fmt(value)} against a limit of {_fmt(limit)}.",
            {"value": value, "threshold": limit},
        )


def _fmt(value: float) -> str:
    return f"{value:.6g}"


class CostUnder(_Threshold):
    name: ClassVar = "cost_under"
    version: ClassVar = 1
    config_model: ClassVar = CostUnderConfig
    unit: ClassVar = "Cost in USD"

    def _figure(self, snapshot: RunSnapshot) -> float | None:
        return _number(snapshot.summary, "estimated_cost_usd")

    def _limit(self, config: Any) -> float:
        return float(config.max_usd)


class LatencyUnder(_Threshold):
    name: ClassVar = "latency_under"
    version: ClassVar = 1
    config_model: ClassVar = LatencyUnderConfig
    unit: ClassVar = "Duration in ms"

    def _figure(self, snapshot: RunSnapshot) -> float | None:
        if snapshot.duration_ms is not None and _is_number(snapshot.duration_ms):
            return float(snapshot.duration_ms)
        return _number(snapshot.summary, "duration_ms")

    def _limit(self, config: Any) -> float:
        return float(config.max_ms)


class ToolErrorRateUnder(_Threshold):
    name: ClassVar = "tool_error_rate_under"
    version: ClassVar = 1
    config_model: ClassVar = ErrorRateConfig
    unit: ClassVar = "Tool error rate"

    def _figure(self, snapshot: RunSnapshot) -> float | None:
        finished = _number(snapshot.summary, "tool_spans_finished")
        ok = _number(snapshot.summary, "tool_spans_ok")
        if not finished or ok is None:
            return None
        return 1 - ok / finished

    def _limit(self, config: Any) -> float:
        return float(config.max_rate)


class RetriesUnder(_Threshold):
    name: ClassVar = "retries_under"
    version: ClassVar = 1
    config_model: ClassVar = CountUnderConfig
    unit: ClassVar = "Retries"

    def _figure(self, snapshot: RunSnapshot) -> float | None:
        return _number(snapshot.summary, "retry_count")

    def _limit(self, config: Any) -> float:
        return float(config.max)


class LlmCallsUnder(_Threshold):
    name: ClassVar = "llm_calls_under"
    version: ClassVar = 1
    config_model: ClassVar = CountUnderConfig
    unit: ClassVar = "Model calls"

    def _figure(self, snapshot: RunSnapshot) -> float | None:
        return _number(snapshot.summary, "llm_calls")

    def _limit(self, config: Any) -> float:
        return float(config.max)


class Human:
    """A person's verdict is submitted, never computed: the registry knows the kind and its
    configuration; a trigger of a human evaluator is refused by the service."""

    name: ClassVar = "human"
    version: ClassVar = 1
    kind: ClassVar = "human"
    config_model: ClassVar = HumanConfig
    metric_keys: ClassVar = frozenset[str]()

    def evaluate(self, snapshot: RunSnapshot, config: Any, budget: Budget) -> EvaluationResult:
        raise EvaluatorError("human evaluators take verdicts, not runs") from None


def _registry(*evaluators: Any) -> Mapping[str, Any]:
    return {e.name: e for e in evaluators}


REGISTRY: Mapping[str, Any] = _registry(
    RunSucceeded(),
    TestsPassed(),
    FilesTouchedOnly(),
    NoPolicyBlocks(),
    ExpectedFilesChanged(),
    CostUnder(),
    LatencyUnder(),
    ToolErrorRateUnder(),
    RetriesUnder(),
    LlmCallsUnder(),
    Human(),
)
