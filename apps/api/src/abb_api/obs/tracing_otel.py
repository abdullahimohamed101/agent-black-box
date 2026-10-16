"""The OpenTelemetry side of tracing (ADR-170). Imported only when tracing is enabled.

This is the one module that imports OpenTelemetry (the SDK and the OTLP exporter come from the
optional ``otel`` extra). Everything that reaches a span passes ``clean_attributes`` first.
"""

import logging
import threading
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Self

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, SpanContext, SpanKind, StatusCode, TraceFlags

from abb_api.obs.tracing import (
    SPAN_NAMES,
    Span,
    clean_attributes,
    valid_traceparent,
)

logger = logging.getLogger(__name__)

# Bounded: a collector that is slow or down loses spans (counted), it never grows memory.
MAX_QUEUE = 2048
MAX_EXPORT_BATCH = 256
SCHEDULE_DELAY_MS = 5000
EXPORT_TIMEOUT_MS = 10_000


class CountingExporter(SpanExporter):
    """Wraps the real exporter: a failure is counted and swallowed, never raised.

    The OTLP exporter's own failure logs name the collector's URL and the response text, so those
    loggers are silenced (``create_tracer``); this wrapper is the one place a failure is seen, and
    it logs the exception *class* once per failure and moves ``otel_export_failures_total``.
    """

    def __init__(self, inner: SpanExporter, failures: Any) -> None:
        self._inner = inner
        self._failures = failures

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            self._failures.inc()
            logger.warning("trace export failed", extra={"error_type": type(exc).__name__})
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            self._failures.inc()
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.warning(
                "trace exporter shutdown failed", extra={"error_type": type(exc).__name__}
            )

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            return False


class _Span:
    def __init__(self, tracer: "OtelTracer", name: str, link: str | None, attrs: dict[str, Any]):
        self._tracer = tracer
        self._name = name
        self._link = link
        self._attrs = attrs
        self._manager: AbstractContextManager[trace.Span] | None = None
        self._span: trace.Span | None = None

    def __enter__(self) -> Self:
        tracer = self._tracer
        if self._name not in SPAN_NAMES:
            if tracer.strict:
                raise AssertionError(f"span name refused: {self._name!r}")
            tracer.refused_attributes += 1
            return self
        links = []
        context = parse_context(self._link)
        if context is not None:
            links.append(Link(context))
        attributes, refused = clean_attributes(self._attrs, strict=tracer.strict)
        tracer.refused_attributes += refused
        try:
            self._manager = tracer.tracer.start_as_current_span(
                self._name,
                kind=SpanKind.SERVER if self._name == "http.request" else SpanKind.INTERNAL,
                links=links,
                attributes=attributes,
                record_exception=False,  # an exception message can quote a payload value
                set_status_on_exception=False,
            )
            self._span = self._manager.__enter__()
        except Exception:  # tracing must never break the work it observes (INV-4)
            self._manager = self._span = None
        return self

    def set(self, **attributes: Any) -> None:
        if self._span is None:
            return
        attrs, refused = clean_attributes(attributes, strict=self._tracer.strict)
        self._tracer.refused_attributes += refused
        try:
            for key, value in attrs.items():
                self._span.set_attribute(key, value)
        except Exception:
            return

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._manager is None or self._span is None:
            return
        try:
            if exc_type is not None:
                # The class only: never the message (status description, exception event).
                self._span.set_status(StatusCode.ERROR)
                self._span.set_attribute("error.type", exc_type.__name__[:64])
            self._manager.__exit__(None, None, None)
        except Exception:
            return


def parse_context(traceparent: str | None) -> SpanContext | None:
    value = valid_traceparent(traceparent)
    if value is None:
        return None
    _, trace_id, span_id, flags = value.split("-")
    return SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(int(flags, 16)),
    )


class OtelTracer:
    def __init__(self, provider: TracerProvider, *, strict: bool = False) -> None:
        self.provider = provider
        self.tracer = provider.get_tracer("abb_api")
        self.strict = strict
        self.refused_attributes = 0

    def start(self, name: str, link: str | None, attributes: dict[str, Any]) -> Span:
        return _Span(self, name, link, attributes)

    def traceparent(self) -> str | None:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}"

    def shutdown(self, timeout_seconds: float) -> None:
        """Flush then shut down, returning after at most ``timeout_seconds``.

        The SDK's flush runs the exporter in the calling thread with no deadline of its own, so a
        collector that accepts the connection and never answers would hold a stop for the
        exporter's whole timeout. The work runs on a daemon thread and is abandoned at the budget
        (spans still queued are lost; that is the cost of a bounded stop).
        """

        def close() -> None:
            self.provider.force_flush(int(max(timeout_seconds, 0.001) * 1000))
            self.provider.shutdown()

        closer = threading.Thread(target=close, name="abb-tracing-shutdown", daemon=True)
        closer.start()
        closer.join(max(timeout_seconds, 0.0))
        if closer.is_alive():
            logger.warning("tracing shutdown abandoned at its time budget")


def create_tracer(
    *,
    service: str,
    sample_ratio: float,
    metrics: Any,
    exporter: SpanExporter | None = None,
    synchronous: bool = False,
    strict: bool = False,
) -> OtelTracer:
    """A tracer that samples ``ParentBased(TraceIdRatioBased(ratio))`` and exports to OTLP/HTTP.

    The collector's endpoint, headers and timeout come from the standard ``OTEL_EXPORTER_OTLP_*``
    variables, read by the exporter. Headers are secrets: nothing here reads or logs them.
    ``exporter``/``synchronous`` are for tests (an in-memory exporter, no batching thread).
    """
    # The exporter's and the SDK's own diagnostics quote the collector URL and response text.
    logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
    resource = Resource.create({SERVICE_NAME: service})
    provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio))
    )
    counted = CountingExporter(exporter or OTLPSpanExporter(), metrics.otel_export_failures_total)
    if synchronous:
        provider.add_span_processor(SimpleSpanProcessor(counted))
    else:
        provider.add_span_processor(
            BatchSpanProcessor(
                counted,
                max_queue_size=MAX_QUEUE,
                max_export_batch_size=MAX_EXPORT_BATCH,
                schedule_delay_millis=SCHEDULE_DELAY_MS,
                export_timeout_millis=EXPORT_TIMEOUT_MS,
            )
        )
    return OtelTracer(provider, strict=strict)
