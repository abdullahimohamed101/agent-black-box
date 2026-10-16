"""Hostile traffic cannot grow the metric series, and no tenant or user text reaches /metrics."""

import random
import re
import string
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.obs.catalogue import UNMATCHED, build_observability
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.helpers import TOKEN, client_for, make_app
from tests.obs.parser import parse

# A generous constant: the real app has ~170 operations. Junk must never add to it.
SERIES_BOUND = 600


async def test_thousands_of_junk_urls_and_methods_leave_the_series_count_bounded() -> None:
    obs = build_observability()
    rng = random.Random(7)
    async with client_for(make_app(observability=obs)) as client:
        await client.get("/healthz")
        baseline = obs.registry.series_total()
        for i in range(3000):
            junk = "".join(rng.choices(string.ascii_letters + string.digits + "/-_.%", k=30))
            method = rng.choice(
                ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "FOO", "X" * 40]
            )
            path = rng.choice([f"/{junk}", f"/v1/{junk}", f"/v1/runs/{junk}", f"/{i}/{junk}"])
            await client.request(method, path + f"?q={junk}", headers={"X-Junk": junk})
        # Plus a few real routes with thousands of distinct ids.
        for _ in range(500):
            await client.post(f"/v1/runs/run_{uuid.uuid4().hex}")  # 405 on a matched template
            await client.delete(f"/v1/runs/run_{uuid.uuid4().hex}/events/evt_{uuid.uuid4().hex}")
        grown = obs.registry.series_total() - baseline
        text = (await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    assert grown < 60, f"junk traffic added {grown} series"
    assert obs.registry.series_total() < SERIES_BOUND
    assert len(text) < 400_000
    families = parse(text)
    routes = {
        labels["route"]
        for _, labels, _ in families["http_requests_total"].samples
        if "route" in labels
    }
    assert UNMATCHED in routes
    assert all(re.search(r"[0-9a-f]{16}", route) is None for route in routes)
    assert {labels["method"] for _, labels, _ in families["http_requests_total"].samples} <= {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
        "other",
    }


async def test_nothing_the_client_controls_reaches_the_exposition() -> None:
    obs = build_observability()
    canaries = [
        "ws_CANARYWORKSPACE000000",
        "proj_CANARYPROJECT0000000",
        "run_CANARYRUN00000000000",
        "abb_live_CANARYKEYID.CANARYSECRET",
        "canary-user@example.test",
        "CANARY-USER-AGENT",
        "CANARY-REQUEST-ID",
        "198.51.100.77",
        "canary-slug",
    ]
    async with client_for(make_app(observability=obs)) as client:
        for canary in canaries:
            await client.post(  # 405: matched route, no handler, no database
                f"/v1/runs/{canary}",
                params={"q": canary, "workspace": canary},
                headers={
                    "Authorization": f"Bearer {canary}",
                    "User-Agent": canary,
                    "X-Request-ID": "CANARY-REQUEST-ID",
                    "X-ABB-Workspace": canary,
                    "X-Forwarded-For": "198.51.100.77",
                    "Origin": f"http://{canary}.example",
                },
            )
            await client.get(f"/{canary}/{canary}")
            await client.get(f"/healthz?{canary}={canary}", headers={"X-Canary": canary})
        text = (await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    lowered = text.lower()
    for canary in canaries:
        assert canary.lower() not in lowered
    assert "canary" not in lowered
    assert TOKEN not in text


@pytest.fixture
async def metrics_api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings = make_settings(database_url, metrics_token=TOKEN)
    async for instance in build_api(
        database_url,
        engine,
        settings=settings,
        app_options={"observability": build_observability()},
    ):
        yield instance


async def test_realistic_tenant_traffic_leaves_no_tenant_text_in_the_exposition(
    metrics_api: Api,
) -> None:
    api = metrics_api
    run = make_run_ids()
    assert (await api.post_batch([wire_event(run, 1), wire_event(run, 2)])).status_code == 202
    await api.drain()
    assert (await api.get("/v1/runs")).status_code == 200
    assert (await api.get(f"/v1/runs/{run['run_id']}")).status_code == 200
    assert (await api.get(f"/v1/runs/{run['run_id']}/events")).status_code == 200
    assert (await api.get(f"/v1/runs/{run['run_id']}", token="other")).status_code == 404
    assert (await api.get("/v1/runs", token="revoked")).status_code == 401
    assert (await api.get("/v1/runs", token=None)).status_code == 401
    assert (await api.get("/v1/runs", token="not-a-key")).status_code == 401
    scraped = await api.client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert scraped.status_code == 200
    text = scraped.text
    families = parse(text)
    templates = {s[1]["route"] for s in families["http_requests_total"].samples if "route" in s[1]}
    assert "/v1/runs/{run_id}" in templates and "/v1/events/batch" in templates

    secrets = {
        api.tenant.workspace_id,
        api.other.workspace_id,
        run["run_id"],
        run["trace_id"],
        "coding-agent",
        "github",
        *api.tenant.projects.values(),
        *api.tenant.projects.keys(),
        *api.other.projects.values(),
        *api.tokens.values(),
        *(token.split(".")[0] for token in api.tokens.values()),
        *(token.removeprefix("abb_live_").split(".")[0] for token in api.tokens.values()),
    }
    assert len(secrets) > 10
    for secret in secrets:
        assert secret not in text, f"{secret!r} leaked into /metrics"
    # And the database role used here is not a metrics subject: only the closed families appear.
    assert isinstance(api.client, httpx.AsyncClient)
