"""The worker's metrics server samples the database gauges on the worker's own event loop."""

import asyncio
import socket

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings
from abb_api.db import create_engine
from abb_api.jobs.outbox import SUMMARIZE_RUN, OutboxRepository
from abb_api.jobs.worker import _start_metrics_server
from abb_api.obs.catalogue import METRICS, build_observability
from abb_api.obs.scrape import ScrapeSampler
from tests.ingest_helpers import make_tenant
from tests.obs.helpers import PLACEHOLDER_DB, TOKEN
from tests.obs.parser import parse, sample_value


async def test_a_worker_scrape_reports_the_queue_it_can_see(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue(
            job_type=SUMMARIZE_RUN, dedupe_key="k", payload={}
        )
    runtime = create_engine(runtime_database_url)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        database_url=PLACEHOLDER_DB,
        metrics_token=TOKEN,  # type: ignore[arg-type]
        worker_metrics_port=port,
    )
    server = _start_metrics_server(
        settings, ScrapeSampler(lambda: runtime, METRICS), asyncio.get_running_loop()
    )
    assert server is not None
    try:
        # The server thread blocks on the loop while it samples, so the request goes off-loop.
        reply = await asyncio.to_thread(
            httpx.get,
            f"http://127.0.0.1:{port}/metrics",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert reply.status_code == 200
        families = parse(reply.text)
        assert sample_value(families, "abb_scrape_database_up") == 1
        assert sample_value(families, "worker_jobs_pending", type=SUMMARIZE_RUN) == 1
    finally:
        server.stop()
        await runtime.dispose()
        METRICS.worker_jobs_pending.clear()
        METRICS.abb_scrape_database_up.set(0)


async def test_a_failing_sampler_never_fails_the_worker_scrape() -> None:
    from pydantic import SecretStr

    from abb_api.obs.server import MetricsServer

    obs = build_observability()

    def boom() -> None:
        raise RuntimeError("database exploded")

    server = MetricsServer(obs.registry, SecretStr(TOKEN), port=0, before_render=boom)
    server.start()
    try:
        reply = await asyncio.to_thread(
            httpx.get,
            f"http://127.0.0.1:{server.port}/metrics",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert reply.status_code == 200
    finally:
        server.stop()
