"""Gate 1: the budgets that moved onto the shared limiter hold across API processes.

A second application object over the same database stands in for a second API process: it has its
own local guards and its own counters object, exactly as another uvicorn worker would.
"""

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.main import create_app
from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.approvals.helpers import decide, intent, publish
from tests.conftest import _client, make_settings
from tests.fake_oidc import FakeOidc
from tests.ingest_helpers import make_run_ids, wire_event
from tests.limits.helpers import room_in_window
from tests.policies.test_policy_api import DOC
from tests.test_alerts_api import create_destination
from tests.test_artifacts_api import new_artifact_id, put
from tests.test_auth_sessions import fake as fake
from tests.test_auth_sessions import oidc_settings


@asynccontextmanager
async def another_process(api: Api, **app_options: Any) -> AsyncIterator[Api]:
    """The same installation served by a second API process."""
    app = create_app(api.app.state.settings, clock=api.clock, **app_options)
    async for client in _client(app):
        yield dataclasses.replace(api, client=client, app=app)


async def counter_rows(engine: AsyncEngine, scope: str) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT coalesce(sum(count), 0) FROM rate_limit_counters WHERE scope = :s"
                    ),
                    {"s": scope},
                )
            ).scalar_one()
        )


def tight_ingest(database_url: str, **extra: Any) -> Any:
    # 0.5 events/s over a 10 s window with a burst of 10: the shared budget is 10 events a window,
    # while each process's own bucket would admit 10 on its own.
    return make_settings(
        database_url,
        ingest_max_batch_events=10,
        rate_limit_burst_events=10,
        rate_limit_events_per_second=0.5,
        **extra,
    )


async def send_alternating(first: Api, second: Api, count: int) -> list[int]:
    codes: list[int] = []
    for i in range(count):
        target = first if i % 2 == 0 else second
        reply = await target.post_batch([wire_event(make_run_ids(), i)])
        codes.append(reply.status_code)
    return codes


async def test_two_processes_share_the_ingestion_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    async for api in build_api(database_url, engine, settings=tight_ingest(database_url)):
        await room_in_window(engine, 10)
        async with another_process(api) as other:
            codes = await send_alternating(api, other, 20)
            refused = await other.post_batch([wire_event(make_run_ids(), 99)])
        assert codes.count(202) == 10 and codes.count(429) == 10  # not 20: one budget, not two
        assert refused.status_code == 429
        assert 1 <= int(refused.headers["retry-after"]) <= 10
        assert refused.json()["error"]["code"] == "RATE_LIMITED"
        # The runtime role (which both applications use) can really write the counters.
        assert await counter_rows(engine, "ingest_events") >= 10


async def test_without_the_shared_tier_two_processes_double_the_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    """The pre-19a behaviour: the off switch, and the control for the test above."""
    settings = tight_ingest(database_url, limits_shared_enabled=False)
    async for api in build_api(database_url, engine, settings=settings):
        async with another_process(api) as other:
            codes = await send_alternating(api, other, 20)
        assert codes.count(202) == 20
        assert await counter_rows(engine, "ingest_events") == 0


async def test_ingestion_keeps_working_and_stays_locally_limited_when_the_counters_fail(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, monkeypatch: Any
) -> None:
    async for api in build_api(database_url, engine, settings=tight_ingest(database_url)):
        counters = api.app.state.shared_counters

        async def broken(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("counters down")

        monkeypatch.setattr(counters, "_upsert", broken)
        codes = [
            (await api.post_batch([wire_event(make_run_ids(), i)])).status_code for i in range(14)
        ]
        assert codes[:10] == [202] * 10  # the host agent's telemetry is not refused
        assert codes[10:] == [429] * 4  # and the local limiter still protects the process
        # One trip per breaker, not per request: the breaker is per scope, and ingestion spends
        # two scopes (events and bytes).
        assert counters.fail_open_events == 2


async def test_two_processes_share_the_policy_write_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, policy_writes_per_minute=3)
    async for api in build_api(database_url, engine, settings=settings):
        headers = await person(api, "owner@acme.test", "OWNER")
        await room_in_window(engine, 60, minimum=10)
        async with another_process(api) as other:
            codes = []
            for i in range(8):
                target = api if i % 2 == 0 else other
                reply = await target.client.post(
                    "/v1/policies/publish", json={"document": DOC}, headers=headers
                )
                codes.append(reply.status_code)
        assert codes.count(201) == 3 and codes.count(429) == 5
        # Another workspace has its own budget.
        globex = await person(api, "boss@globex.test", "OWNER", workspace="globex")
        reply = await api.client.post(
            "/v1/policies/publish", json={"document": DOC}, headers=globex
        )
        assert reply.status_code == 201


async def test_two_processes_share_the_alert_write_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, alerts_writes_per_minute=3)
    async for api in build_api(database_url, engine, settings=settings):
        headers = await person(api, "owner@acme.test", "OWNER")
        await room_in_window(engine, 60, minimum=10)
        async with another_process(api) as other:
            codes = []
            for i in range(8):
                target = api if i % 2 == 0 else other
                codes.append((await create_destination(target, headers, f"d{i}")).status_code)
        assert codes.count(201) == 3 and codes.count(429) == 5


async def test_two_processes_share_the_decision_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        policy_decide_per_minute_key=6,
        policy_decide_per_minute_workspace=1000,
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        await room_in_window(engine, 60, minimum=10)
        async with another_process(api) as other:
            codes = []
            for i in range(14):
                target = api if i % 2 == 0 else other
                codes.append((await decide(target, intent(resource=str(i)))).status_code)
        assert codes.count(200) == 6 and codes.count(429) == 8


async def test_two_processes_share_the_sign_in_start_ceiling(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, fake: FakeOidc
) -> None:
    settings = oidc_settings(database_url, login_global_per_minute=3)
    async for api in build_api(
        database_url,
        engine,
        settings=settings,
        clock=fake.clock,  # type: ignore[arg-type]
        app_options={"oidc_transport": fake.transport()},
    ):
        await room_in_window(engine, 60, minimum=10)
        async with another_process(api, oidc_transport=fake.transport()) as other:
            codes = []
            for i in range(8):
                target = api if i % 2 == 0 else other
                codes.append((await target.client.get("/v1/auth/login")).status_code)
        assert codes.count(302) == 3 and codes.count(429) == 5
        # The counter is a global scope: stored under the nil workspace, never a tenant's.
        async with engine.connect() as conn:
            workspaces = (
                (
                    await conn.execute(
                        text(
                            "SELECT DISTINCT workspace_id::text FROM rate_limit_counters "
                            "WHERE scope = 'login_start'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert workspaces == ["00000000-0000-0000-0000-000000000000"]


async def test_artifact_uploads_have_their_own_shared_scope_and_bytes_are_charged(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    settings = make_settings(
        database_url,
        artifact_dir=str(tmp_path / "s"),
        rate_limit_burst_events=1000,
        rate_limit_bytes_per_second=100_000,  # 1 MB per 10 s window, 8 MiB burst floor
        rate_limit_burst_bytes=8 * 1024 * 1024,
    )
    async for api in build_api(database_url, engine, settings=settings):
        async with engine.begin() as conn:
            keys = ApiKeyRepository(conn, api.tenant.context)
            token = (
                await keys.create(
                    scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                    project_id=api.tenant.project_uuids["alpha"],
                )
            ).token
        await room_in_window(engine, 10)
        async with another_process(api) as other:
            statuses = []
            for i in range(6):
                target = api if i % 2 == 0 else other
                statuses.append(
                    (
                        await put(target, new_artifact_id(), b"x" * 2_000_000, token=token)
                    ).status_code
                )
        # 8 MiB per window across both processes: 4 x 2 MB fit, the rest are refused.
        assert statuses.count(201) == 4 and statuses.count(429) == 2
        assert await counter_rows(engine, "artifact_count") >= 4
        assert await counter_rows(engine, "ingest_events") == 0  # a separate scope from events
