"""Cost bounds on the HTTP path (AC-7): budget, concurrency cap, starvation, degraded facets."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.runs.queries import RunQueries
from abb_api.search.reads import SearchBudget, SearchGate
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.search.http_helpers import Spec, ingest_spec

READER = "wide_reader"
TOOL: Spec = [("run.started", {}, {}), ("tool.call.completed", {"tool.name": "github"}, {})]

MakeApi = Callable[..., Awaitable[Api]]


@pytest.fixture
async def make_api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[MakeApi]:
    generators: list[Any] = []

    async def make(**settings: Any) -> Api:
        generator = build_api(
            database_url, engine, settings=make_settings(database_url, **settings)
        )
        generators.append(generator)
        return await generator.__anext__()

    yield make
    for generator in generators:
        await generator.aclose()


def sleepy_page(seconds: float, then_real: bool = False) -> Any:
    original = RunQueries.page

    async def page(self: RunQueries, *args: Any, **kwargs: Any) -> Any:
        budget = kwargs.get("budget")
        if budget is None:  # the plain listing is not slowed
            return await original(self, *args, **kwargs)
        await budget.run(self._conn, text(f"SELECT pg_sleep({seconds})"))
        return await original(self, *args, **kwargs) if then_real else []

    return page


async def active_sleepers(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' "
                        "AND query LIKE '%pg_sleep%' AND pid <> pg_backend_pid() "
                        "AND datname = current_database()"
                    )
                )
            ).scalar_one()
        )


async def test_a_search_past_its_budget_is_a_retryable_503(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = await make_api(search_timeout_seconds=0.05)
    monkeypatch.setattr(RunQueries, "page", sleepy_page(2))
    response = await api.get("/v1/runs", token=READER, q="tool:github")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "SEARCH_TIMEOUT" and error["retryable"] is True
    assert api.app.state.engine.pool.checkedout() == 0  # the connection is back in the pool


async def test_a_search_runs_in_a_read_only_transaction(
    engine: AsyncEngine, make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-101 (review F4): a bug in a compiled statement cannot write. A write attempted through
    the budget's connection fails, in the unit and on the request path."""
    budget = SearchBudget(5.0)
    write = text("UPDATE run_search SET error_count = error_count WHERE false")
    async with engine.connect() as conn:
        await budget.run(conn, text("SELECT 1"))
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await conn.execute(write)

    api = await make_api()
    original = RunQueries.page

    async def writing_page(self: RunQueries, *args: Any, **kwargs: Any) -> Any:
        budget = kwargs.get("budget")
        if budget is not None:
            await budget.run(self._conn, text("SELECT 1"))  # arms the read-only transaction
            await self._conn.execute(write)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(RunQueries, "page", writing_page)
    response = await api.get("/v1/runs", token=READER, q="tool:github")
    assert response.status_code >= 500, response.text
    assert api.app.state.engine.pool.checkedout() == 0


async def test_a_flood_gets_timeouts_and_one_busy_while_ingestion_and_plain_reads_are_fine(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine
) -> None:
    # cap 4: one actor may hold two permits (ADR-158), so three concurrent searches from one key
    # are two admitted and one refused
    api = await make_api(search_timeout_seconds=1.5, search_max_concurrent=4)
    monkeypatch.setattr(RunQueries, "page", sleepy_page(5))

    async def search() -> Any:
        return await api.get("/v1/runs", token=READER, q="tool:github")

    flood = [asyncio.create_task(search()) for _ in range(3)]
    await asyncio.sleep(0.5)  # the two admitted searches are inside pg_sleep now
    ingest = await api.post_batch([wire_event(make_run_ids(), 1)])
    plain = await api.get("/v1/runs", token=READER)
    results = await asyncio.gather(*flood)
    assert ingest.status_code == 202 and plain.status_code == 200
    codes = sorted(r.json()["error"]["code"] for r in results)
    assert codes == ["SEARCH_BUSY", "SEARCH_TIMEOUT", "SEARCH_TIMEOUT"]
    busy = next(r for r in results if r.json()["error"]["code"] == "SEARCH_BUSY")
    assert busy.status_code == 503 and busy.headers["retry-after"] == "1"
    assert busy.json()["error"]["retryable"] is True
    assert busy.json()["error"]["details"] == {"reason": "actor_limit"}
    assert await active_sleepers(engine) == 0
    assert api.app.state.search_gate.held == 0  # every permit was released
    assert api.app.state.engine.pool.checkedout() == 0
    again = await api.get("/v1/runs", token=READER, q="tool:github")  # permits came back
    assert again.status_code == 503 and again.json()["error"]["code"] == "SEARCH_TIMEOUT"


async def test_facets_are_left_out_when_the_page_used_most_of_the_budget(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = await make_api(search_timeout_seconds=1.0)
    await ingest_spec(api, TOOL)
    await api.drain()
    monkeypatch.setattr(RunQueries, "page", sleepy_page(0.7, then_real=True))
    response = await api.get("/v1/runs", token=READER, q="tool:github")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["facets"] is None and body["facets_skipped"] == "budget"
    assert body["query"]["unindexed"] is None  # the count rides with the facets statement


async def test_a_facets_statement_cut_off_by_the_budget_degrades_instead_of_failing(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A budget wide enough that the page itself never trips it on a loaded machine; the facets
    # statement sleeps far past it, so only the facets are cut.
    api = await make_api(search_timeout_seconds=4.0)
    await ingest_spec(api, TOOL)
    await api.drain()

    async def slow_facets(self: RunQueries, search: Any, *, budget: SearchBudget, **kw: Any) -> Any:
        await budget.run(self._conn, text("SELECT pg_sleep(30)"))

    monkeypatch.setattr(RunQueries, "facets_and_unindexed", slow_facets)
    response = await api.get("/v1/runs", token=READER, q="tool:github")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1 and body["facets"] is None
    assert body["facets_skipped"] == "budget"


async def test_the_plain_listing_takes_no_permit_and_has_no_timeout(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = await make_api(search_max_concurrent=1)
    gate_users = 0

    async def blocked_page(self: RunQueries, *args: Any, **kwargs: Any) -> Any:
        nonlocal gate_users
        gate_users += 1
        await asyncio.sleep(0.3)
        return []

    monkeypatch.setattr(RunQueries, "page", blocked_page)
    responses = await asyncio.gather(
        *(api.get("/v1/runs", token=READER, q=q) for q in ("", "", " "))
    )
    assert [r.status_code for r in responses] == [200, 200, 200] and gate_users == 3


async def test_a_search_starved_of_a_connection_answers_timeout_within_its_budget(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KI-082: the wait for a pooled connection used to sit outside the budget, so the permit was
    held for the budget plus the pool's 5 s and the answer was DEPENDENCY_UNAVAILABLE."""
    import time
    from contextlib import asynccontextmanager

    api = await make_api(search_timeout_seconds=0.4)
    engine = api.app.state.engine
    pool = engine.pool
    held: list[Any] = []
    original = SearchGate.request

    @asynccontextmanager
    async def starving(self: SearchGate, workspace_id: Any, actor: str) -> Any:
        # Authentication has its connection already; ingestion now takes every other one, as
        # it would under load, after the permit is granted and before the search connects.
        async with original(self, workspace_id, actor) as budget:
            while pool.checkedout() < pool.size() + pool._max_overflow:
                held.append(await engine.connect())
            yield budget

    monkeypatch.setattr(SearchGate, "request", starving)
    try:
        started = time.monotonic()
        response = await api.get("/v1/runs", token=READER, q="tool:github")
        elapsed = time.monotonic() - started
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SEARCH_TIMEOUT"
        assert elapsed < 2.0, elapsed  # about the budget, far below pool_timeout (5 s)
        assert api.app.state.search_gate.held == 0
    finally:
        for conn in held:
            await conn.close()
    assert pool.checkedout() == 0
