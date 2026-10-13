"""A flood of decision calls is bounded and cannot starve ordinary requests (gate 8, KI-018)."""

import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import build_api
from tests.approvals.helpers import ask, decide, intent, publish
from tests.conftest import make_settings


async def test_a_flood_gets_429_with_retry_after_and_ordinary_requests_are_unaffected(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url,
        web_origin="http://localhost:3000",
        policy_decide_per_minute_key=30,
        policy_decide_per_minute_workspace=100,
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        calls = [decide(api, intent(capability="x.y", resource=str(i))) for i in range(200)]
        started = time.monotonic()
        flood = asyncio.gather(*calls)
        ordinary_times: list[float] = []
        for _ in range(10):
            t0 = time.monotonic()
            reply = await api.client.get("/v1/runs", headers=api.headers("reader"))
            ordinary_times.append(time.monotonic() - t0)
            assert reply.status_code == 200
        results = await flood
        statuses = [r.status_code for r in results]
        assert statuses.count(200) == 30  # exactly the key's burst
        assert statuses.count(429) == 170 and set(statuses) == {200, 429}
        refused = next(r for r in results if r.status_code == 429)
        assert refused.headers["retry-after"].isdigit() and int(refused.headers["retry-after"]) >= 1
        assert refused.json()["error"]["code"] == "RATE_LIMITED"
        assert refused.json()["error"]["retryable"] is True
        assert max(ordinary_times) < 2.0 and time.monotonic() - started < 30
        # Ingestion by the same project key is a separate budget and is still accepted.
        assert (await api.post_batch([], token="writer")).status_code in (202, 400)


async def test_the_workspace_budget_bounds_many_keys_together(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url,
        web_origin="http://localhost:3000",
        policy_decide_per_minute_key=1000,
        policy_decide_per_minute_workspace=40,
    )
    async for api in build_api(database_url, engine, settings=settings):
        results = await asyncio.gather(
            *(
                decide(api, intent(resource=str(i)), "writer" if i % 2 else "ingest_only")
                for i in range(100)
            )
        )
        statuses = [r.status_code for r in results]
        assert statuses.count(200) == 40 and statuses.count(429) == 60
        # Another workspace has its own budget.
        assert (await decide(api, intent(), "other")).status_code == 200


async def test_waiting_and_spending_count_against_the_same_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url, web_origin="http://localhost:3000", policy_decide_per_minute_key=5
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        _, approval_id = await ask(api)  # one
        reads = [
            await api.client.get(
                f"/v1/approvals/{approval_id}/wait?timeout=0", headers=api.headers("writer")
            )
            for _ in range(6)
        ]
        assert [r.status_code for r in reads] == [200, 200, 200, 200, 429, 429]
