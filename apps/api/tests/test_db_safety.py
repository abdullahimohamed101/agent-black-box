"""Phase 19c step 3: the database circuit breaker and the default statement timeout (ADR-169).

Deterministic by construction: the breaker runs on an injected clock, "fails fast" is asserted by
counting calls that reach the pool (never by wall time), and the statement timeouts are
server-side cancellations of `pg_sleep` against a limit a tenth of the sleep.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from abb_api.core.errors import is_connectivity_error, is_statement_timeout
from abb_api.db import create_engine
from abb_api.db.breaker import BreakerOpen, DbBreaker
from abb_api.main import create_app
from abb_api.obs.catalogue import Observability, build_observability
from tests.conftest import UNREACHABLE_DATABASE_URL, _client, make_settings
from tests.obs.helpers import value


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def breaker_with(clock: Clock, obs: Observability, **kw: Any) -> DbBreaker:
    options: dict[str, Any] = {"threshold": 3, "open_seconds": 5.0, "window_seconds": 30.0}
    options.update(kw)
    return DbBreaker(clock=clock, metrics=obs.catalogue, **options)


# ------------------------------------------------------------------ the state machine


def test_opens_after_threshold_consecutive_failures_and_counts_the_trip() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    for _ in range(2):
        breaker.record_failure()
        breaker.before_connect()  # still closed
    breaker.record_failure()
    assert str(breaker.state) == "open"
    assert value(obs, "db_breaker_state") == 2
    assert value(obs, "db_breaker_trips_total") == 1
    with pytest.raises(BreakerOpen) as raised:
        breaker.before_connect()
    assert raised.value.retry_after_seconds == 5


def test_a_success_resets_the_count_and_old_failures_expire() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert str(breaker.state) == "closed"  # not consecutive
    clock.now += 31  # the window passed: the next failure starts a new count
    breaker.record_failure()
    assert str(breaker.state) == "closed"
    assert breaker.trips == 0


def test_half_open_lets_one_probe_through_and_its_outcome_decides() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 5
    assert str(breaker.state) == "half_open"
    breaker.before_connect()  # the probe
    with pytest.raises(BreakerOpen):
        breaker.before_connect()  # everyone else is still shed while it is in flight
    breaker.record_failure()  # the probe failed: open again, for a full period
    assert str(breaker.state) == "open"
    assert breaker.trips == 2
    clock.now += 5
    breaker.before_connect()
    breaker.record_success()
    assert str(breaker.state) == "closed"
    assert value(obs, "db_breaker_state") == 0
    breaker.before_connect()


def test_a_probe_that_never_reports_is_replaced_after_one_more_period() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 5
    breaker.before_connect()  # probe 1 is lost (its request was cancelled)
    clock.now += 4
    with pytest.raises(BreakerOpen):
        breaker.before_connect()
    clock.now += 1
    breaker.before_connect()  # probe 2


def test_stragglers_do_not_extend_or_close_an_open_breaker() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 2
    breaker.record_failure()  # a checkout that started before it opened and failed late
    breaker.record_success()  # ... or succeeded late
    clock.now += 3  # five seconds since it opened, not since the straggler
    assert breaker.state == "half_open"


def test_kill_switch_never_sheds() -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs, enabled=False)
    for _ in range(50):
        breaker.record_failure()
    breaker.before_connect()
    assert str(breaker.state) == "closed"
    assert breaker.trips == 0


# ------------------------------------------------------------------ through the real pool


class PoolCalls:
    """Counts checkouts that reach the pool and can make them fail like an outage would."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reached = 0
        self.fail = False
        original = AsyncAdaptedQueuePool.connect

        def connect(pool: AsyncAdaptedQueuePool) -> Any:
            self.reached += 1
            if self.fail:
                raise ConnectionRefusedError("injected outage")
            return original(pool)

        monkeypatch.setattr(AsyncAdaptedQueuePool, "connect", connect)


async def checkout(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def test_a_failing_pool_opens_the_breaker_then_fails_fast_then_recovers(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs)
    pool = PoolCalls(monkeypatch)
    engine = create_engine(database_url, breaker=breaker)
    try:
        pool.fail = True
        for _ in range(3):
            with pytest.raises(ConnectionRefusedError):
                await checkout(engine)
        assert pool.reached == 3
        for _ in range(20):  # shed: not one of these touches the pool
            with pytest.raises(BreakerOpen):
                await checkout(engine)
        assert pool.reached == 3
        pool.fail = False
        clock.now += 5  # half-open: exactly one probe reaches the pool and closes it
        await checkout(engine)
        assert pool.reached == 4
        assert str(breaker.state) == "closed"
        await checkout(engine)
        assert pool.reached == 5
    finally:
        await engine.dispose()


async def test_an_unreachable_database_opens_the_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs, threshold=2)
    pool = PoolCalls(monkeypatch)
    engine = create_engine(UNREACHABLE_DATABASE_URL, breaker=breaker)
    try:
        for _ in range(2):
            with pytest.raises(OSError):
                await checkout(engine)
        with pytest.raises(BreakerOpen) as raised:
            await checkout(engine)
        assert is_connectivity_error(raised.value)
        assert pool.reached == 2
    finally:
        await engine.dispose()


async def test_the_kill_switch_keeps_every_checkout_reaching_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs, enabled=False)
    pool = PoolCalls(monkeypatch)
    pool.fail = True
    engine = create_engine(UNREACHABLE_DATABASE_URL, breaker=breaker)
    try:
        for _ in range(10):
            with pytest.raises(ConnectionRefusedError):
                await checkout(engine)
        assert pool.reached == 10
    finally:
        await engine.dispose()


async def test_slow_queries_statement_timeouts_and_app_errors_never_trip_it(
    database_url: str,
) -> None:
    clock, obs = Clock(), build_observability()
    breaker = breaker_with(clock, obs, threshold=2)
    engine = create_engine(database_url, breaker=breaker, statement_timeout_seconds=0.05)
    try:
        for _ in range(6):  # more than the threshold, every one cancelled by the server
            with pytest.raises(DBAPIError) as raised:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT pg_sleep(2)"))
            assert is_statement_timeout(raised.value)
            assert not is_connectivity_error(raised.value)
        for _ in range(3):  # an application error after a successful checkout
            with pytest.raises(DBAPIError):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1/0"))
        assert str(breaker.state) == "closed"
        assert breaker.trips == 0
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ statement timeouts


async def test_the_default_statement_timeout_cancels_a_slow_statement(database_url: str) -> None:
    engine = create_engine(database_url, statement_timeout_seconds=0.1)
    try:
        async with engine.connect() as conn:
            shown = (await conn.execute(text("SHOW statement_timeout"))).scalar_one()
        assert shown == "100ms"
        with pytest.raises(DBAPIError) as raised:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT pg_sleep(3)"))
        assert is_statement_timeout(raised.value)
    finally:
        await engine.dispose()


async def test_a_budgeted_route_sets_its_own_timeout_which_wins(database_url: str) -> None:
    engine = create_engine(database_url, statement_timeout_seconds=0.05)
    try:
        async with engine.begin() as conn:  # the pattern of search, analytics, alert preview
            await conn.execute(text("SET LOCAL statement_timeout = 5000"))
            await conn.execute(text("SELECT pg_sleep(0.3)"))  # would die at the default
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout = 50"))
            with pytest.raises(DBAPIError) as raised:
                await conn.execute(text("SELECT pg_sleep(3)"))
        assert is_statement_timeout(raised.value)
    finally:
        await engine.dispose()


async def test_off_means_no_default_and_the_serving_default_is_30_seconds(
    database_url: str,
) -> None:
    plain = create_engine(database_url)  # what the CLIs, retention, backup and rotation build
    app = create_app(make_settings(database_url))
    try:
        async with plain.connect() as conn:
            assert (await conn.execute(text("SHOW statement_timeout"))).scalar_one() == "0"
        async for _ in _client(app):
            async with app.state.engine.connect() as conn:
                shown = (await conn.execute(text("SHOW statement_timeout"))).scalar_one()
            assert shown == "30s"
    finally:
        await plain.dispose()


def test_migrations_and_cli_engines_do_not_use_the_serving_default() -> None:
    root = Path(__file__).resolve().parents[1]
    env = (root / "migrations" / "env.py").read_text()
    assert "statement_timeout" not in env
    assert "create_engine" not in env.replace("create_async_engine", "")  # its own engine
    for module in ("cli.py", "retention/runner.py"):
        source = (root / "src" / "abb_api" / module).read_text()
        assert "db_statement_timeout_seconds" not in source, module


async def test_the_default_timeout_surfaces_as_a_fixed_504(database_url: str) -> None:
    app = create_app(make_settings(database_url, db_statement_timeout_seconds=0.1))

    @app.get("/slow")
    async def slow(request: Request) -> None:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT pg_sleep(3) /* secret-sql-marker */"))

    async for c in _client(app):
        response = await c.get("/slow")
    assert response.status_code == 504
    error = response.json()["error"]
    assert error["code"] == "DATABASE_TIMEOUT"
    assert error["category"] == "TIMEOUT"
    assert "secret-sql-marker" not in response.text
    assert "pg_sleep" not in response.text


# ------------------------------------------------------------------ the API under an outage


@pytest.fixture
async def down_app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, PoolCalls]]:
    pool = PoolCalls(monkeypatch)
    app = create_app(
        make_settings(UNREACHABLE_DATABASE_URL, db_breaker_threshold=3, db_breaker_open_seconds=60),
        observability=build_observability(),
    )

    @app.get("/needs-db")
    async def needs_db(request: Request) -> dict[str, str]:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": "yes"}

    async for c in _client(app):
        yield app, c, pool


async def test_an_outage_is_answered_fast_with_retry_after_and_liveness_stays_green(
    down_app: tuple[Any, httpx.AsyncClient, PoolCalls],
) -> None:
    app, client, pool = down_app
    breaker: DbBreaker = app.state.db_breaker
    while breaker.state != "open":  # the startup role check already spent one failure
        first = await client.get("/needs-db")
        assert first.status_code == 503
    reached = pool.reached
    for _ in range(10):
        shed = await client.get("/needs-db")
        assert shed.status_code == 503
        assert shed.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert 1 <= int(shed.headers["retry-after"]) <= 60
        assert "breaker" not in shed.text.lower()
    assert pool.reached == reached  # none of the ten waited on the pool
    assert (await client.get("/healthz")).status_code == 200
    ready = await client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["database"] == {"status": "fail", "reason": "breaker_open"}
    assert pool.reached == reached  # readiness did not touch the pool either
    assert json.dumps(ready.json()).count("127.0.0.1") == 0


async def test_breaker_state_is_a_metric(
    down_app: tuple[Any, httpx.AsyncClient, PoolCalls],
) -> None:
    app, client, _ = down_app
    while app.state.db_breaker.state != "open":
        await client.get("/needs-db")
    catalogue = app.state.observability.catalogue
    assert catalogue.db_breaker_state.value() == 2
    assert catalogue.db_breaker_trips_total.value() == 1
