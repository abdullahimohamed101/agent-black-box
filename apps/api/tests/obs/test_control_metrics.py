"""The 19a controls and the sessions, driven for real; each series moves by exactly one step."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.active_keys import ActiveKeyIds
from abb_api.core.body_budget import BodyBudget, BodyBudgetMiddleware
from abb_api.core.errors import install_error_handlers
from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from abb_api.limits import Scope, SharedCounters, SharedLimiterUnavailable
from abb_api.obs.catalogue import Observability, build_observability
from abb_api.search.reads import SearchGate
from tests.api_fixtures import WEB_ORIGIN, Api, Tick, build_api
from tests.auth_helpers import add_member, mint_session
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings
from tests.fake_oidc import FakeOidc
from tests.ingest_helpers import make_run_ids, wire_event
from tests.limits.helpers import pooled, room_in_window
from tests.obs.helpers import TOKEN, total, value
from tests.obs.parser import parse, sample_value
from tests.test_auth_sessions import (
    CLIENT_ID,
    ISSUER,
    REDIRECT,
    Browser,
    oidc_settings,
    refused_sign_in,
)
from tests.test_auth_throttle import probe, throttle_settings, unknown_key, wrong_secret

MIB = 1024 * 1024
BATCH = "/v1/events/batch"


async def build(
    database_url: str, engine: AsyncEngine, obs: Observability, **settings: Any
) -> AsyncIterator[Api]:
    config = make_settings(database_url, metrics_token=TOKEN, **settings)
    async for api in build_api(
        database_url, engine, settings=config, app_options={"observability": obs}
    ):
        yield api


# ------------------------------------------------------------------ quotas


async def test_quota_refusals_are_counted_by_quota_in_a_batch_and_on_the_single_endpoint(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    async for api in build(database_url, engine, obs, quota_events_per_day=2):
        run = make_run_ids()
        reply = await api.post_batch([wire_event(run, n) for n in range(1, 5)])
        assert reply.status_code == 202 and reply.json()["accepted"] == 2
        assert value(obs, "quota_refusals_total", quota="events_per_day") == 2
        assert value(obs, "telemetry_events_rejected_total", reason="quota_exceeded") == 2
        assert value(obs, "telemetry_events_received_total") == 2
        # The single-event endpoint raises it as a 413: still one count per refused event.
        single = await api.client.post("/v1/events", json=wire_event(run, 9), headers=api.headers())
        assert single.status_code == 413 and single.json()["error"]["code"] == "QUOTA_EXCEEDED"
        assert value(obs, "quota_refusals_total", quota="events_per_day") == 3
        assert value(obs, "telemetry_events_rejected_total", reason="quota_exceeded") == 3
        assert total(obs, "quota_refusals_total") == 3
        assert value(obs, "ingest_acks_total", outcome="rejected") == 1


async def test_a_rate_limited_batch_is_a_rate_limited_rejection_not_an_error(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    limiter = InMemoryRateLimiter(
        events_per_second=0.001, burst_events=1, bytes_per_second=10**9, burst_bytes=10**9
    )
    config = make_settings(database_url, metrics_token=TOKEN)
    async for api in build_api(
        database_url,
        engine,
        settings=config,
        rate_limiter=limiter,
        app_options={"observability": obs},
    ):
        reply = await api.post_batch([wire_event(make_run_ids(), 1), wire_event(make_run_ids(), 1)])
        assert reply.status_code == 429
        assert value(obs, "telemetry_events_rejected_total", reason="rate_limited") == 1
        assert value(obs, "ingest_acks_total", outcome="rejected") == 1
        assert value(obs, "ingest_acks_total", outcome="error") == 0


# ------------------------------------------------------------------ body cap


def body_app(obs: Observability, *, cap: int, ceiling: int = 8 * MIB) -> tuple[FastAPI, BodyBudget]:
    budget = BodyBudget(cap, metrics=obs.catalogue)
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/body")
    async def body(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    app.add_middleware(
        BodyBudgetMiddleware, budget=budget, ceiling_bytes=ceiling, read_timeout_seconds=30
    )
    return app, budget


def client_of(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_body_cap_refusals_by_kind_and_the_in_flight_gauge() -> None:
    obs = build_observability()
    app, budget = body_app(obs, cap=4 * MIB, ceiling=2 * MIB)
    async with client_of(app) as client:
        # ceiling: a body over the hard ceiling.
        assert (await client.post("/body", content=b"x" * (3 * MIB))).status_code == 413
        assert value(obs, "body_cap_refusals_total", reason="ceiling") == 1
        # budget: other requests hold the memory; this one is refused up front.
        budget.in_flight = 3 * MIB
        text = obs.registry.render()
        assert sample_value(parse(text), "body_bytes_in_flight") == 3 * MIB
        assert (await client.post("/body", content=b"x" * (2 * MIB))).status_code == 503
        assert value(obs, "body_cap_refusals_total", reason="budget") == 1
        # Small bodies are never refused for the budget and nothing else moved.
        assert (await client.post("/body", content=b"small")).status_code == 200
        assert total(obs, "body_cap_refusals_total") == 2
        budget.in_flight = 0
    assert sample_value(parse(obs.registry.render()), "body_bytes_in_flight") == 0


async def test_a_body_that_never_finishes_is_counted_as_a_timeout() -> None:
    obs = build_observability()
    budget = BodyBudget(64 * MIB, metrics=obs.catalogue)
    sent: list[dict[str, Any]] = []

    async def read_all(scope: Any, receive: Any, send: Any) -> None:
        while (await receive()).get("more_body"):
            pass

    async def send(message: Any) -> None:
        sent.append(message)

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await queue.put({"type": "http.request", "body": b"x", "more_body": True})
    app = BodyBudgetMiddleware(read_all, budget=budget, ceiling_bytes=MIB, read_timeout_seconds=0.2)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"transfer-encoding", b"chunked")],
    }
    with pytest.raises(Exception, match="not received in time"):
        await app(scope, queue.get, send)
    assert value(obs, "body_cap_refusals_total", reason="timeout") == 1


# ------------------------------------------------------------------ limiter


async def test_database_calls_are_counted_ok_and_equal_the_counters_own_tally(
    database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5, metrics=obs.catalogue)
        ws = uuid.uuid4()
        for _ in range(7):
            await counters.check_and_increment(Scope.POLICY_WRITE, "w", 100, 3600, workspace_id=ws)
    assert counters.database_calls == 7
    assert value(obs, "limiter_database_calls_total", outcome="ok") == 7
    assert value(obs, "limiter_database_calls_total", outcome="error") == 0
    assert total(obs, "limiter_fail_open_total") == 0


async def test_an_unreachable_database_counts_errors_and_fail_opens_per_scope() -> None:
    from abb_api.db import create_engine

    obs = build_observability()
    engine = create_engine(UNREACHABLE_DATABASE_URL)
    try:
        counters = SharedCounters(
            engine, timeout_seconds=0.5, backoff_seconds=60, metrics=obs.catalogue
        )
        for _ in range(3):
            with pytest.raises(SharedLimiterUnavailable):
                await counters.check_and_increment(Scope.LOGIN_START, "k", 10, 60)
        with pytest.raises(SharedLimiterUnavailable):
            await counters.check_and_increment(
                Scope.POLICY_WRITE, "k", 10, 60, workspace_id=uuid.uuid4()
            )
    finally:
        await engine.dispose()
    # One real attempt per scope; the breaker answers the other two login decisions from memory.
    assert value(obs, "limiter_database_calls_total", outcome="error") == 2
    assert value(obs, "limiter_database_calls_total", outcome="ok") == 0
    assert value(obs, "limiter_fail_open_total", scope="login_start") == 3
    assert value(obs, "limiter_fail_open_total", scope="policy_write") == 1
    assert counters.fail_open_events == 2


async def test_a_limiter_scope_the_catalogue_does_not_know_folds_into_other() -> None:
    obs = build_observability()
    obs.catalogue.limiter_fail_open_total.inc("tenant-secret-scope")
    assert value(obs, "limiter_fail_open_total", scope="other") == 1


# ------------------------------------------------------------------ failed-auth throttle


async def test_throttle_blocks_are_counted_per_scope_and_from_memory(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    settings = throttle_settings(database_url, auth_fail_per_credential=2, metrics_token=TOKEN)
    async for api in build_api(
        database_url, engine, settings=settings, app_options={"observability": obs}
    ):
        await room_in_window(engine, 60)
        real = wrong_secret(api.tokens["writer"])
        assert [(await probe(api, real)).status_code for _ in range(2)] == [401, 401]
        assert value(obs, "auth_throttle_blocks_total", kind="credential") == 0
        assert (await probe(api, real)).status_code == 429  # the failure that used the budget up
        assert (await probe(api, real)).status_code == 429  # and one refused from memory
        assert value(obs, "auth_throttle_blocks_total", kind="credential") == 2
        assert total(obs, "auth_throttle_blocks_total") == 2
        # Active key ids: the set the throttle consults has the usable keys (7 of the fixture's 9).
        assert value(obs, "auth_active_keys") == len(api.app.state.auth_throttle.active_keys) == 7
        assert value(obs, "auth_active_key_refresh_failures_total") == 0


async def test_address_blocks_have_their_own_kind(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    settings = throttle_settings(
        database_url, auth_fail_per_address=2, auth_fail_global=1000, metrics_token=TOKEN
    )
    async for api in build_api(
        database_url, engine, settings=settings, app_options={"observability": obs}
    ):
        await room_in_window(engine, 60)
        for _ in range(2):
            assert (await probe(api, unknown_key(), "203.0.113.9")).status_code == 401
        assert (await probe(api, unknown_key(), "203.0.113.9")).status_code == 429
        assert value(obs, "auth_throttle_blocks_total", kind="address") == 1
        assert value(obs, "auth_throttle_blocks_total", kind="global") == 0


async def test_global_blocks_have_their_own_kind(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs2 = build_observability()
    settings = throttle_settings(database_url, auth_fail_global=3, metrics_token=TOKEN)
    async for api in build_api(
        database_url, engine, settings=settings, app_options={"observability": obs2}
    ):
        await room_in_window(engine, 60)
        codes = [(await probe(api, unknown_key())).status_code for _ in range(5)]
        assert codes[:3] == [401] * 3 and set(codes[3:]) == {429}
        assert value(obs2, "auth_throttle_blocks_total", kind="global") == 2


async def test_a_failing_refresh_of_the_active_key_set_is_counted_and_keeps_the_old_set() -> None:
    obs = build_observability()
    broken = {"fail": False}

    async def load(limit: int) -> list[str]:
        if broken["fail"]:
            raise OSError("down")
        return ["key_a", "key_b"]

    keys = ActiveKeyIds(load, metrics=obs.catalogue)
    assert await keys.refresh() is True
    assert value(obs, "auth_active_keys") == 2
    broken["fail"] = True
    assert await keys.refresh() is False
    assert value(obs, "auth_active_key_refresh_failures_total") == 1
    assert value(obs, "auth_active_keys") == 2, "the previous set keeps serving"
    keys.add("key_c")
    assert value(obs, "auth_active_keys") == 3


# ------------------------------------------------------------------ search


async def test_search_busy_by_reason_and_permits_in_use(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    async for api in build(database_url, engine, obs):
        gate = SearchGate(max_concurrent=1, timeout_seconds=5, metrics=obs.catalogue)
        api.app.state.search_gate = gate
        release = asyncio.Event()

        async def hold(gate: SearchGate = gate, release: asyncio.Event = release) -> None:
            async with gate.request(uuid.uuid4(), "key:someone"):
                await release.wait()

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        assert value(obs, "search_permits_in_use") == 1
        reply = await api.get("/v1/runs", token="reader", q="status:failed")
        assert reply.status_code == 503 and reply.json()["error"]["code"] == "SEARCH_BUSY"
        assert value(obs, "search_busy_total", reason="process_full") == 1
        assert total(obs, "search_busy_total") == 1
        release.set()
        await holder
        assert value(obs, "search_permits_in_use") == 0


# ------------------------------------------------------------------ sessions and invitations


async def test_session_revocations_count_logout_one_and_others(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    obs = build_observability()
    async for api in build(database_url, engine, obs, web_origin=WEB_ORIGIN):
        user = await add_member(
            api.engine, api.tenant.context.workspace_id, "ada@example.com", "OWNER"
        )
        tokens = [await mint_session(api.engine, user.id, api.clock()) for _ in range(5)]

        def cookie(token: str) -> dict[str, str]:
            return {"cookie": f"abb_session={token}", "origin": WEB_ORIGIN}

        listed = (await api.client.get("/v1/auth/sessions", headers=cookie(tokens[0]))).json()
        victim = next(s["id"] for s in listed["items"] if not s["current"])
        gone = await api.client.delete(f"/v1/auth/sessions/{victim}", headers=cookie(tokens[0]))
        assert gone.status_code == 204
        assert value(obs, "session_revocations_total", reason="revoke_one") == 1
        others = await api.client.post("/v1/auth/sessions/revoke-others", headers=cookie(tokens[0]))
        assert others.json()["revoked"] == 3
        assert value(obs, "session_revocations_total", reason="revoke_others") == 3
        # A refused revoke of an unknown session is not a revocation.
        missing = await api.client.delete(
            f"/v1/auth/sessions/{uuid.uuid4()}", headers=cookie(tokens[0])
        )
        assert missing.status_code == 404
        out = await api.client.post("/v1/auth/logout", headers=cookie(tokens[0]))
        assert out.status_code == 200
        assert value(obs, "session_revocations_total", reason="logout") == 1
        # Signing out with no valid session revokes nothing.
        await api.client.post("/v1/auth/logout", headers=cookie(tokens[0]))
        assert value(obs, "session_revocations_total", reason="logout") == 1
        assert total(obs, "session_revocations_total") == 5


# ------------------------------------------------------------------ invitation-only


@pytest.fixture
def fake() -> FakeOidc:
    return FakeOidc(issuer=ISSUER, client_id=CLIENT_ID, redirect_uris=(REDIRECT,), clock=Tick())


@pytest.fixture
async def oidc_app(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, fake: FakeOidc
) -> AsyncIterator[tuple[Api, Observability]]:
    obs = build_observability()
    assert isinstance(fake.clock, Tick)
    async for api in build_api(
        database_url,
        engine,
        settings=oidc_settings(database_url, metrics_token=TOKEN),
        clock=fake.clock,
        app_options={"oidc_transport": fake.transport(), "observability": obs},
    ):
        yield api, obs


async def test_a_sign_in_without_an_invitation_counts_one_refusal_and_an_invited_one_none(
    oidc_app: tuple[Api, Observability], fake: FakeOidc
) -> None:
    api, obs = oidc_app
    for n in range(3):
        refused = await refused_sign_in(api, fake, f"stranger{n}@example.com")
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "NO_ACCESS"
    assert value(obs, "invitation_only_refusals_total") == 3
    assert (await Browser(api, fake).sign_in("friend@example.com")).status_code == 302
    assert value(obs, "invitation_only_refusals_total") == 3, "an invited person is not a refusal"
