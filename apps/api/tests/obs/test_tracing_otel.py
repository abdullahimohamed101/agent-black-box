"""Optional tracing with the OpenTelemetry SDK's in-memory exporter (D9 gate 5, ADR-170).

Needs the `otel` extra (`uv sync --extra otel`); skipped with that reason when it is absent. The CI
quality job installs the extra so these always run there.
"""

import json
import threading
from collections.abc import Iterator, Sequence
from typing import Any

import pytest

pytest.importorskip(
    "opentelemetry.sdk", reason="the optional 'otel' extra is not installed (uv sync --extra otel)"
)

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from abb_api.alerts.delivery import deliver_due
from abb_api.alerts.notifiers import build_registry
from abb_api.jobs.outbox import OutboxRepository
from abb_api.jobs.worker import Worker
from abb_api.obs import tracing
from abb_api.obs.catalogue import Catalogue
from abb_api.obs.tracing_otel import OtelTracer, create_tracer
from abb_api.retention.runner import RetentionRunnerSettings, run_once
from tests.alerts_helpers import seed_destination, seed_rule
from tests.alerts_receivers import TlsReceiver
from tests.api_fixtures import Api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.log_capture import capture_everything, find_leaks
from tests.test_alerts_delivery import T0, dsettings, pending

EXPORTED_KEYS = {exported for exported, _ in tracing.ATTRIBUTES.values()}


class Rig:
    def __init__(self, tracer: OtelTracer, exporter: InMemorySpanExporter) -> None:
        self.tracer, self.exporter = tracer, exporter

    @property
    def spans(self) -> list[ReadableSpan]:
        return list(self.exporter.get_finished_spans())

    def named(self, name: str) -> list[ReadableSpan]:
        return [s for s in self.spans if s.name == name]


def make_rig(metrics: Catalogue, ratio: float = 1.0, **options: Any) -> Rig:
    exporter = InMemorySpanExporter()
    tracer = create_tracer(
        service="abb-test",
        sample_ratio=ratio,
        metrics=metrics,
        exporter=exporter,
        synchronous=True,
        strict=True,  # a call site that sets a non-allow-listed attribute fails the test
        **options,
    )
    return Rig(tracer, exporter)


@pytest.fixture
def rig(api: Api) -> Iterator[Rig]:
    made = make_rig(api.app.state.observability.catalogue)
    tracing.install(made.tracer)
    yield made
    tracing.install(None)


def attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def assert_attributes_are_allow_listed(spans: Sequence[ReadableSpan]) -> None:
    assert spans
    for span in spans:
        for key, value in attrs(span).items():
            assert key in EXPORTED_KEYS, (span.name, key)
            if isinstance(value, str):
                assert len(value) <= 64, (span.name, key)
            else:
                assert isinstance(value, int) and not isinstance(value, bool), (span.name, key)
        for event in span.events:
            raise AssertionError(f"span {span.name} has an event: {event.name}")  # no exceptions
        assert span.status.description is None


async def ingest(api: Api, **fields: Any) -> tuple[int, str]:
    run = make_run_ids()
    reply = await api.post_batch([wire_event(run, 1, **fields), wire_event(run, 2)])
    return reply.status_code, reply.headers["x-request-id"]


# ------------------------------------------------------------------ the request


async def test_the_request_span_carries_the_route_template_method_and_status_only(
    api: Api, rig: Rig
) -> None:
    status, _ = await ingest(api, payload={"prompt": "SENTINEL-SPAN-PAYLOAD"})
    await api.get("/v1/runs/run_SENTINEL-SPAN-PATH", token="reader", q="SENTINEL-SPAN-QUERY")
    assert status == 202
    requests = rig.named("http.request")
    ingest_span = next(s for s in requests if attrs(s).get("http.route") == "/v1/events/batch")
    assert attrs(ingest_span) == {
        "http.request.method": "POST",
        "http.route": "/v1/events/batch",
        "http.response.status_code": 202,
    }
    other = next(s for s in requests if s is not ingest_span)
    assert attrs(other)["http.route"] == "/v1/runs/{run_id}"
    assert_attributes_are_allow_listed(rig.spans)
    dump = json.dumps(
        [[s.name, attrs(s), s.resource.attributes.get("service.name")] for s in rig.spans]
    )
    for sentinel in ("SENTINEL-SPAN", "run_SENTINEL", api.tokens["writer"]):
        assert sentinel not in dump


async def test_persistence_and_checkout_spans_nest_under_the_request(api: Api, rig: Rig) -> None:
    await ingest(api)
    (request,) = [
        s for s in rig.named("http.request") if attrs(s)["http.route"] == "/v1/events/batch"
    ]
    (persist,) = rig.named("ingest.persist")
    assert persist.parent is not None and persist.parent.span_id == request.context.span_id
    assert attrs(persist) == {"abb.events": 2, "abb.accepted": 2}
    assert persist.context.trace_id == request.context.trace_id
    checkouts = [
        s for s in rig.named("db.checkout") if s.context.trace_id == request.context.trace_id
    ]
    assert checkouts and all(attrs(s) == {} for s in checkouts)  # no SQL, no parameters
    assert request.context.is_valid and request.kind.name == "SERVER"


async def test_an_inbound_traceparent_is_ignored(api: Api, rig: Rig) -> None:
    forged = "00-" + "ee" * 16 + "-" + "ff" * 8 + "-01"
    run = make_run_ids()
    await api.client.post(
        "/v1/events/batch",
        json={"events": [wire_event(run, 1)]},
        headers={**api.headers(), "traceparent": forged},
    )
    for span in rig.spans:
        assert f"{span.context.trace_id:032x}" != "ee" * 16
    assert all(s.parent is None for s in rig.named("http.request"))


# ------------------------------------------------------------------ the job link


async def test_the_job_span_links_to_the_request_through_the_payload(api: Api, rig: Rig) -> None:
    await ingest(api)
    (request,) = [
        s for s in rig.named("http.request") if attrs(s)["http.route"] == "/v1/events/batch"
    ]
    async with api.engine.connect() as conn:
        payloads = [
            r[0]
            for r in await conn.exec_driver_sql(
                "SELECT payload FROM outbox_jobs WHERE job_type = 'summarize_run'"
            )
        ]
    (payload,) = payloads
    carried = payload["traceparent"]
    # The context of the span that enqueued the job: ingest.persist, a child of the request span.
    (persist,) = rig.named("ingest.persist")
    assert carried.startswith(f"00-{request.context.trace_id:032x}-{persist.context.span_id:016x}-")
    assert len(carried) == 55 and tracing.valid_traceparent(carried) == carried
    assert set(payload) == {"run_id", "traceparent"}

    await api.drain()
    jobs = [s for s in rig.named("job.run") if attrs(s).get("abb.job.type") == "summarize_run"]
    (job,) = jobs
    assert attrs(job) == {
        "abb.job.type": "summarize_run",
        "abb.job.attempt": 1,
        "abb.outcome": "succeeded",
    }
    (link,) = job.links
    assert (link.context.trace_id, link.context.span_id) == (
        request.context.trace_id,
        persist.context.span_id,
    )
    assert (
        job.parent is None and job.context.trace_id != request.context.trace_id
    )  # a link, not a child
    assert_attributes_are_allow_listed(rig.spans)


async def test_a_bad_payload_traceparent_yields_a_job_span_without_a_link(
    api: Api, rig: Rig
) -> None:
    async def explode(job: Any, conn: Any) -> None:
        raise RuntimeError("SENTINEL-SPAN-EXCEPTION")

    worker = Worker(
        api.worker._engine,
        {"boom": explode},
        make_settings("postgresql+asyncpg://x:y@localhost/z", worker_max_attempts=2),
        owner="span-test",
        metrics=api.app.state.observability.catalogue,
    )
    async with api.engine.begin() as conn:
        outbox = OutboxRepository(conn, api.tenant.context)
        await outbox.enqueue(
            job_type="boom", dedupe_key="a", payload={"traceparent": "x" * 5000, "n": 1}
        )
    await worker.run_once()
    (job,) = rig.named("job.run")
    assert job.links == () and attrs(job)["abb.outcome"] == "retried"
    assert attrs(job)["error.type"] == "RuntimeError"  # the class; never the message
    assert "SENTINEL-SPAN-EXCEPTION" not in json.dumps(
        [attrs(s) for s in rig.spans] + [str(s.status) for s in rig.spans]
    )
    assert_attributes_are_allow_listed(rig.spans)


# ------------------------------------------------------------------ other spans


async def test_an_alert_delivery_attempt_names_only_the_destination_type(
    api: Api, rig: Rig
) -> None:
    async with TlsReceiver(responder=lambda _: (500, {})) as receiver:
        config = dsettings(api, alerts_max_attempts=1)
        destination = await seed_destination(
            api.engine, api.tenant.context, url=receiver.url("/hook/SENTINELSECRETTOKEN1234")
        )
        rule = await seed_rule(api.engine, api.tenant.context, [destination])
        await pending(api, rule, destination)
        async with api.engine.begin() as conn:
            await deliver_due(
                conn,
                api.tenant.context,
                now=T0,
                settings=config,
                registry=build_registry(config, receiver.sender()),
                rng=lambda: 0.5,
            )
    (attempt,) = rig.named("alerts.attempt")
    assert attrs(attempt) == {"abb.alert.destination_type": "webhook", "abb.outcome": "failed"}
    assert "SENTINELSECRETTOKEN1234" not in json.dumps([attrs(s) for s in rig.spans])
    assert_attributes_are_allow_listed(rig.spans)


async def test_a_retention_tick_span_names_only_its_mode(
    api: Api, rig: Rig, database_url: str
) -> None:
    settings = RetentionRunnerSettings(
        retention_mode="dry_run", environment="test", database_url=database_url
    )
    await run_once(settings)
    (tick,) = rig.named("retention.tick")
    assert attrs(tick) == {"abb.retention.mode": "dry_run", "abb.outcome": "ok"}


# ------------------------------------------------------------------ sampling


async def test_sampling_zero_exports_nothing_and_one_exports_everything(api: Api) -> None:
    metrics = api.app.state.observability.catalogue
    off = make_rig(metrics, ratio=0.0)
    tracing.install(off.tracer)
    try:
        await ingest(api)
        await api.drain()
        assert off.spans == []
        # Unsampled work still has a context to carry; only recording is skipped.
        with tracing.span("http.request", method="GET"):
            assert tracing.current_traceparent() is not None
    finally:
        tracing.install(None)
    on = make_rig(metrics, ratio=1.0)
    tracing.install(on.tracer)
    try:
        await ingest(api)
        assert {"http.request", "ingest.persist"} <= {s.name for s in on.spans}
    finally:
        tracing.install(None)


# ------------------------------------------------------------------ the allow-list at the SDK


def test_a_call_site_with_an_unknown_attribute_or_name_fails_under_strict(api: Api) -> None:
    made = make_rig(api.app.state.observability.catalogue)
    with pytest.raises(AssertionError):
        with made.tracer.start("http.request", None, {"path": "/v1/runs/run_X"}):
            pass
    with pytest.raises(AssertionError):
        with made.tracer.start("made.up.name", None, {}):
            pass
    with pytest.raises(AssertionError):
        with made.tracer.start("job.run", None, {}) as inner:
            inner.set(job_type="x" * 65)


def test_outside_strict_a_refused_attribute_is_dropped_and_counted_never_raised(api: Api) -> None:
    metrics = api.app.state.observability.catalogue
    made = make_rig(metrics)
    made.tracer.strict = False
    with made.tracer.start("job.run", None, {"path": "/x", "job_type": "ok"}) as inner:
        inner.set(workspace_id="ws_01", outcome="succeeded", status=99999)
    (finished,) = made.spans
    assert attrs(finished) == {"abb.job.type": "ok", "abb.outcome": "succeeded"}
    assert made.tracer.refused_attributes == 3


# ------------------------------------------------------------------ failures never reach a request


class BrokenExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("SENTINEL-COLLECTOR-DOWN http://collector.invalid:4318")

    def shutdown(self) -> None:
        raise OSError("SENTINEL-COLLECTOR-DOWN")


async def test_a_failing_exporter_never_breaks_a_request_and_is_counted(api: Api) -> None:
    metrics = api.app.state.observability.catalogue
    tracer = create_tracer(
        service="abb-test",
        sample_ratio=1.0,
        metrics=metrics,
        exporter=BrokenExporter(),
        synchronous=True,
    )
    tracing.install(tracer)
    try:
        with capture_everything() as captured:
            before = metrics.otel_export_failures_total.value()
            status, _ = await ingest(api)
            await api.drain()
        assert status == 202
        assert metrics.otel_export_failures_total.value() > before
        assert find_leaks(captured, {"SENTINEL-COLLECTOR-DOWN", "collector.invalid"}) == {}
        assert any(r.getMessage() == "trace export failed" for r in captured.records)
    finally:
        tracing.install(None)
        tracer.shutdown(1.0)


async def test_the_real_exporter_failing_is_counted_and_headers_are_never_logged(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "Bearer SENTINEL-OTLP-HEADER-VALUE"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={secret}")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    metrics = api.app.state.observability.catalogue
    with capture_everything() as captured:
        tracer = create_tracer(service="abb-test", sample_ratio=1.0, metrics=metrics)
        tracing.install(tracer)
        try:
            status, _ = await ingest(api)
        finally:
            tracing.install(None)
            tracer.shutdown(10.0)  # flushes: the export fails, is counted, is swallowed
    assert status == 202
    assert metrics.otel_export_failures_total.value() >= 1
    assert find_leaks(captured, {"SENTINEL-OTLP-HEADER-VALUE", "127.0.0.1:9"}) == {}


def test_shutdown_flushes_pending_spans_and_is_bounded_when_the_collector_is_stuck(
    api: Api,
) -> None:
    metrics = api.app.state.observability.catalogue
    exported: list[str] = []

    class Recording(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            exported.extend(s.name for s in spans)
            return SpanExportResult.SUCCESS

    tracer = create_tracer(service="t", sample_ratio=1.0, metrics=metrics, exporter=Recording())
    with tracer.start("job.run", None, {"job_type": "x"}):
        pass
    tracer.shutdown(5.0)
    assert exported == ["job.run"]  # flushed before shutdown returned

    release = threading.Event()
    entered = threading.Event()

    class Stuck(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            entered.set()
            release.wait(60)
            return SpanExportResult.SUCCESS

    stuck = create_tracer(service="t", sample_ratio=1.0, metrics=metrics, exporter=Stuck())
    with stuck.start("job.run", None, {}):
        pass
    try:
        stuck.shutdown(0.2)  # returns although the exporter is still blocked
        assert entered.wait(5)  # the export really was in progress
        assert not release.is_set()  # ...and shutdown did not wait for it
    finally:
        release.set()


# ------------------------------------------------------------------ no second source of spans


async def test_the_frameworks_native_telemetry_never_starts_an_exporter(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.telemetry import _runtime

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    before = list(_runtime._owned)
    await ingest(api)
    assert list(_runtime._owned) == before == []
