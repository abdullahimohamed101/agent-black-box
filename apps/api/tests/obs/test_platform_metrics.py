"""Alerts, the live stream, the connection pool and the scrape endpoint with real traffic."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from abb_api.alerts.delivery import deliver_due
from abb_api.db import create_engine
from abb_api.obs.catalogue import Observability, build_observability
from abb_api.streaming.limits import StreamLimiter
from tests.alerts_receivers import Request
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.helpers import TOKEN, observations, total, value
from tests.obs.parser import parse, sample_value
from tests.stream_fixtures import Live, messages, send, serve
from tests.test_alerts_delivery import HALF, T0, Rig, rig_factory  # noqa: F401


async def tick(rig: Rig, obs: Observability, at: Any = T0, **settings: Any) -> Any:
    used = rig.settings.model_copy(update=settings) if settings else rig.settings
    async with rig.api.engine.begin() as conn:
        return await deliver_due(
            conn,
            rig.tenant,
            now=at,
            settings=used,
            registry=rig.registry,
            rng=HALF,
            metrics=obs.catalogue,
        )


async def test_alert_deliveries_by_status_and_fixed_reason(rig_factory: Any) -> None:  # noqa: F811
    answers = iter([(503, {}), (200, {}), (400, {})])

    def responder(_: Request) -> tuple[int, dict[str, str]]:
        return next(answers)

    obs = build_observability()
    rig: Rig = await rig_factory(responder)
    (first,) = await rig.add()
    assert (await tick(rig, obs)).retried == 1  # 503: a retryable failure
    assert value(obs, "alert_deliveries_total", status="failed", reason="http_5xx") == 1
    assert (await tick(rig, obs, T0 + timedelta(minutes=5))).delivered == 1  # 200
    assert value(obs, "alert_deliveries_total", status="delivered", reason="none") == 1
    (second,) = await rig.add()
    assert (await tick(rig, obs, T0 + timedelta(minutes=6))).dead == 1  # 400: no retry will fix it
    assert value(obs, "alert_deliveries_total", status="dead", reason="http_4xx") == 1
    assert total(obs, "alert_deliveries_total") == 3
    assert first != second


async def test_skipped_deliveries_carry_their_reason(rig_factory: Any) -> None:  # noqa: F811
    obs = build_observability()
    rig: Rig = await rig_factory()
    await rig.add(2)
    result = await tick(rig, obs, alerts_rate_per_minute=1)
    assert (result.delivered, result.skipped) == (1, 1)
    assert value(obs, "alert_deliveries_total", status="delivered", reason="none") == 1
    assert value(obs, "alert_deliveries_total", status="skipped", reason="rate_limited") == 1
    assert total(obs, "alert_deliveries_total") == 2


async def test_dead_letters_and_destination_states_are_sampled_at_scrape_time(
    rig_factory: Any,  # noqa: F811
    runtime_database_url: str,
) -> None:
    from abb_api.obs.scrape import ScrapeSampler

    obs = build_observability()
    rig: Rig = await rig_factory(lambda _: (400, {}))
    await rig.add(1)
    await tick(rig, obs)
    runtime = create_engine(runtime_database_url)
    try:
        await ScrapeSampler(lambda: runtime, obs.catalogue).refresh()
    finally:
        await runtime.dispose()
    assert value(obs, "alert_dead_letters") == 1
    assert (
        sum(value(obs, "alert_destinations", state=s) for s in ("active", "paused", "failing")) == 1
    )
    # No destination name, address or id is a label.
    assert {name for name, *_ in obs.registry.metrics["alert_destinations"].collect()} == {""}


# ------------------------------------------------------------------ the live stream


def test_stream_connections_follow_the_leases_exactly() -> None:
    obs = build_observability()
    limiter = StreamLimiter(5, 3, obs.catalogue)
    leases = [limiter.acquire("k1"), limiter.acquire("k1"), limiter.acquire("k2")]
    assert value(obs, "sse_connections") == 3
    leases[0].release()
    leases[0].release()  # releasing twice is harmless and counted once
    assert value(obs, "sse_connections") == 2
    for lease in leases[1:]:
        lease.release()
    assert value(obs, "sse_connections") == 0
    # A refused stream took nothing.
    full = StreamLimiter(1, 1, obs.catalogue)
    full.acquire("a")
    with pytest.raises(Exception, match="Too many open streams"):
        full.acquire("b")
    assert value(obs, "sse_connections") == 1


@pytest.fixture
async def live(
    observed: tuple[Api, Observability], runtime_database_url: str
) -> AsyncIterator[tuple[Live, Observability]]:
    api, obs = observed
    async for instance in serve(api, runtime_database_url, observability=obs):
        yield instance, obs


async def test_a_live_stream_counts_one_connection_and_one_lag_observation_per_event(
    live: tuple[Live, Observability],
) -> None:
    server, obs = live
    run = make_run_ids()
    for n in (1, 2, 3):
        await send(server.api, run, n)
    seen = 0
    async with server.open(run["run_id"]) as response:
        assert response.status_code == 200
        assert value(obs, "sse_connections") == 1
        async for message in messages(response):
            if message.get("event") == "trace_event":
                seen += 1
            if seen == 3:
                break
    assert observations(obs, "sse_delivery_lag_seconds") == 3
    for _ in range(100):  # the server releases the slot when it notices the client left
        if value(obs, "sse_connections") == 0:
            break
        await asyncio.sleep(0.05)
    assert value(obs, "sse_connections") == 0


# ------------------------------------------------------------------ the pool


async def test_every_checkout_is_observed_once(
    database_url: str, runtime_database_url: str, engine: Any
) -> None:
    obs = build_observability()
    pooled = create_engine(runtime_database_url, pool_wait=obs.catalogue.postgres_pool_wait_seconds)
    try:
        before = observations(obs, "postgres_pool_wait_seconds")
        for _ in range(4):
            async with pooled.connect() as conn:
                await conn.execute(text("SELECT 1"))
        assert observations(obs, "postgres_pool_wait_seconds") - before == 4

        # Concurrent checkouts each count once, whether they wait or not.
        async def use() -> None:
            async with pooled.connect() as conn:
                await conn.execute(text("SELECT pg_sleep(0.01)"))

        await asyncio.gather(*(use() for _ in range(6)))
        assert observations(obs, "postgres_pool_wait_seconds") - before == 10
    finally:
        await pooled.dispose()


# ------------------------------------------------------------------ the scrape endpoint


async def test_the_scrape_serves_database_gauges_and_every_new_family_parses(
    observed: tuple[Api, Observability],
) -> None:
    api, _ = observed
    run = make_run_ids()
    assert (await api.post_batch([{"nonsense": True}])).status_code == 202
    assert (await api.post_batch([])).status_code == 400
    # A job is pending (the summarizer has not run): the scrape reports it, from the database.
    assert (await api.post_batch([wire_event(run, 1)])).status_code == 202
    reply = await api.client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert reply.status_code == 200
    families = parse(reply.text)
    assert sample_value(families, "abb_scrape_database_up") == 1
    assert sample_value(families, "worker_jobs_pending", type="summarize_run") == 1
    lag = sample_value(families, "worker_job_lag_seconds", type="summarize_run")
    assert lag is not None and lag >= 0
    assert sample_value(families, "ingest_acks_total", outcome="committed") == 2
    assert sample_value(families, "ingest_acks_total", outcome="rejected") == 1
    assert sample_value(families, "telemetry_events_rejected_total", reason="invalid") == 1
    assert (
        sample_value(families, "telemetry_events_rejected_total", reason="schema_unsupported") == 1
    )
    # The application's own pool is timed: its checkouts show up in the histogram.
    count = sample_value(families, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count")
    assert count is not None and count > 0
    # A second scrape within the window does not touch the database again (cached).
    sampler = api.app.state.scrape_sampler
    attempts = sampler.samples
    await api.client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert sampler.samples == attempts == 1
