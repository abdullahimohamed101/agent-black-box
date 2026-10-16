"""Optional tracing behind a tiny interface (spec §116.3, ADR-170).

Call sites say ``with span("job.run", job_type=..., link=traceparent) as s:`` and never import
OpenTelemetry. While tracing is off (the default) ``span()`` returns one shared no-op object: no
span, no attribute dictionary kept, nothing imported. OpenTelemetry lives in ``tracing_otel.py``,
which is imported only by ``start()`` when ``ABB_OTEL_ENABLED=1``.

What a span may carry is closed here, in code, and enforced before anything reaches the SDK:

* span names come from ``SPAN_NAMES``;
* attribute keys come from ``ATTRIBUTES``, each with a validator that accepts only a member of a
  closed set, a small number, or a short identifier of the kinds our code produces (a route
  template, a job type, an exception *class* name). A string value is at most 64 characters;
* nothing a client controls (payloads, names, slugs, ids, paths, queries, headers, SQL) has a key.
  Tenant identifiers are not attributes either (decided in ADR-170, as for metrics).

A rejected attribute is dropped (and counted on the tracer); it never raises into a request
(INV-4), except under ``strict`` which the tests switch on so a new call site cannot slip through.
"""

import importlib
import logging
import re
from collections.abc import Callable
from typing import Any, Final, Protocol, Self

logger = logging.getLogger(__name__)

MAX_STRING = 64
TRACEPARENT_LENGTH = 55
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_{}/.:\-]*$")

HTTP_METHODS: Final = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "other"}
)
OUTCOMES: Final = frozenset(
    {
        "succeeded",
        "retried",
        "dead_letter",
        "lease_lost",
        "failed",
        "delivered",
        "dead",
        "deferred",
        "ok",
        "error",
        "dry_run",
        "enforce",
        "off",
    }
)
SPAN_NAMES: Final = frozenset(
    {
        "http.request",
        "ingest.persist",
        "job.run",
        "db.checkout",
        "alerts.attempt",
        "retention.tick",
    }
)
# Libraries that start spans of their own must not run beside ours (see ADR-170 on FastAPI).
LIBRARY_INSTRUMENTATION_DISABLED: Final = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


def _one_of(values: frozenset[str]) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and v in values


def _number(low: int, high: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and low <= v <= high


def _text(pattern: re.Pattern[str]) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and len(v) <= MAX_STRING and pattern.match(v) is not None


# key -> (exported name, accepts). The exported names follow OpenTelemetry's HTTP conventions
# where one exists; the rest are ours under `abb.`.
ATTRIBUTES: Final[dict[str, tuple[str, Callable[[Any], bool]]]] = {
    "method": ("http.request.method", _one_of(HTTP_METHODS)),
    "route": ("http.route", _text(_ROUTE)),
    "status": ("http.response.status_code", _number(100, 599)),
    "job_type": ("abb.job.type", _text(_IDENTIFIER)),
    "attempt": ("abb.job.attempt", _number(0, 1000)),
    "outcome": ("abb.outcome", _one_of(OUTCOMES)),
    "destination_type": ("abb.alert.destination_type", _text(_IDENTIFIER)),
    "error_type": ("error.type", _text(_IDENTIFIER)),
    "events": ("abb.events", _number(0, 1_000_000)),
    "accepted": ("abb.accepted", _number(0, 1_000_000)),
    "mode": ("abb.retention.mode", _one_of(OUTCOMES)),
}


def clean_attributes(
    attributes: dict[str, Any], *, strict: bool = False
) -> tuple[dict[str, Any], int]:
    """The exportable attributes, and how many were refused (unknown key or unacceptable value)."""
    kept: dict[str, Any] = {}
    refused = 0
    for key, value in attributes.items():
        entry = ATTRIBUTES.get(key)
        if entry is None or not entry[1](value):
            if strict:
                raise AssertionError(f"span attribute refused: {key!r}")
            refused += 1
            continue
        kept[entry[0]] = value
    return kept, refused


def valid_traceparent(value: object) -> str | None:
    """A W3C ``traceparent`` (version 00) with non-zero ids, or None. Untrusted job payload data."""
    if not isinstance(value, str) or len(value) != TRACEPARENT_LENGTH:
        return None
    match = _TRACEPARENT.match(value)
    if match is None or int(match[1], 16) == 0 or int(match[2], 16) == 0:
        return None
    return value


def traceparent_in(payload: object) -> str | None:
    """The validated ``traceparent`` a producer stored in a job payload, if any."""
    if isinstance(payload, dict):
        return valid_traceparent(payload.get("traceparent"))
    return None


class Span(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

    def set(self, **attributes: Any) -> None:
        """Add attributes (allow-listed; see ``ATTRIBUTES``)."""
        ...


class _NoopSpan:
    """The one span every call returns while tracing is off."""

    __slots__ = ()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def set(self, **attributes: Any) -> None:
        return None


NOOP: Final = _NoopSpan()


class Tracer(Protocol):
    refused_attributes: int

    def start(self, name: str, link: str | None, attributes: dict[str, Any]) -> Span: ...

    def traceparent(self) -> str | None: ...

    def shutdown(self, timeout_seconds: float) -> None: ...


_tracer: Tracer | None = None


class TracingUnavailable(RuntimeError):
    """ABB_OTEL_ENABLED is set but the optional dependencies are not installed."""


def span(name: str, /, *, link: str | None = None, **attributes: Any) -> Span:
    """A span (a context manager), or the shared no-op while tracing is off."""
    tracer = _tracer
    if tracer is None:
        return NOOP
    return tracer.start(name, link, attributes)


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` of the current span, to carry in a job payload; None when off."""
    tracer = _tracer
    return None if tracer is None else tracer.traceparent()


def enabled() -> bool:
    return _tracer is not None


def install(tracer: Tracer | None) -> None:
    global _tracer
    _tracer = tracer


def start(settings: Any, *, service: str, metrics: Any) -> None:
    """Turn tracing on when asked. Raises TracingUnavailable (a fixed message) without the extra."""
    if not settings.otel_enabled or _tracer is not None:
        return
    try:
        tracing_otel = importlib.import_module("abb_api.obs.tracing_otel")
    except ImportError:
        raise TracingUnavailable(
            "ABB_OTEL_ENABLED is set but the optional tracing packages are not installed. "
            "Install the 'otel' extra (uv sync --extra otel, or pip install 'abb-api[otel]') "
            "or unset ABB_OTEL_ENABLED."
        ) from None
    install(
        tracing_otel.create_tracer(
            service=service,
            sample_ratio=settings.otel_sample_ratio,
            metrics=metrics,
        )
    )


def stop(timeout_seconds: float) -> None:
    """Flush and shut down within ``timeout_seconds``; never raises."""
    tracer = _tracer
    install(None)
    if tracer is not None:
        try:
            tracer.shutdown(timeout_seconds)
        except Exception as exc:  # shutting down must not turn a clean stop into a crash
            logger.warning("tracing shutdown failed", extra={"error_type": type(exc).__name__})
