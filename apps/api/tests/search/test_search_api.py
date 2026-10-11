"""Search semantics on a real PostgreSQL (AC-3): the compiler's predicates over seeded runs.

Runs are created through the real ingestion path and summarizer, so the documents are the ones
production would write. Step 4 adds the HTTP surface to this file.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from abb_event_schema.event import Event
from abb_event_schema.ids import to_uuid
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.db import tables as t
from abb_api.runs.queries import RunQueries
from abb_api.runs.repository import RunRepository
from abb_api.search.compiler import compile_query
from abb_api.search.parser import parse
from abb_api.search.reads import SearchBudget, SearchGate
from tests.ingest_helpers import T0, Tenant, build_event, make_run_ids, make_tenant
from tests.test_summarizer import drain, ingest, worker_for

NOW = T0 + timedelta(hours=1)


def principal(tenant: Tenant, *held: str) -> Principal:
    return Principal(
        kind="user",
        workspace_id=tenant.context.workspace_id,
        project_id=None,
        actions=frozenset({actions.RUN_READ, *held}),
        actor_id="user:test",
    )


def at(days_ago: int, n: int) -> str:
    moment = T0 - timedelta(days=days_ago) + timedelta(seconds=n)
    return moment.isoformat().replace("+00:00", "Z")


class Seeded:
    def __init__(self, tenant: Tenant, ids: dict[str, uuid.UUID]) -> None:
        self.tenant = tenant
        self.ids = ids
        self.by_id = {v: k for k, v in ids.items()}

    def names(self, records: list[Any]) -> set[str]:
        return {self.by_id[r.id] for r in records}


def run_events(
    tenant: Tenant,
    run: dict[str, str],
    days_ago: int,
    spec: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> list[Event]:
    events = []
    for n, (kind, attributes, extra) in enumerate(spec, start=1):
        events.append(
            build_event(
                tenant,
                run,
                n=n,
                event_type=kind,
                attributes=attributes,
                occurred_at=at(days_ago, n),
                **extra,
            )
        )
    return events


LLM = {"llm.provider": "prov", "llm.model": "m1", "llm.input_tokens": 7}
SEEDS: dict[str, tuple[int, list[tuple[str, dict[str, Any], dict[str, Any]]]]] = {
    "fail": (
        0,
        [
            ("run.started", {"run.name": "Fix login"}, {"tags": ["prod"]}),
            ("tool.call.completed", {"tool.name": "github"}, {}),
            ("llm.request.completed", {**LLM, "cost.estimated_usd": 0.5}, {"status": "success"}),
            ("retry.attempted", {"retry.attempt": 1}, {}),
            ("retry.attempted", {"retry.attempt": 2}, {}),
            (
                "shell.command.completed",
                {"shell.command": "curl -H 'Authorization: Bearer s3cr3t' https://x.test"},
                {},
            ),
            ("tool.call.failed", {"tool.name": "x"}, {"status": "error"}),
            ("run.failed", {}, {}),
        ],
    ),
    "ok": (
        1,
        [
            ("run.started", {"run.name": "Deploy API"}, {}),
            ("tool.call.completed", {"tool.name": "slack"}, {}),
            ("llm.request.completed", {**LLM, "cost.estimated_usd": 2.0}, {"status": "success"}),
            ("shell.command.completed", {"shell.command": "npm test -- src/auth.test.ts"}, {}),
            ("file.modified", {"file.path": "/home/x/src/auth/login.ts"}, {}),
            ("run.completed", {}, {}),
        ],
    ),
    "running": (
        2,
        [
            ("run.started", {}, {}),
            ("tool.call.completed", {"tool.name": "github"}, {}),
        ],
    ),
    "secure": (
        3,
        [
            ("run.started", {}, {}),
            ("policy.action.blocked", {"policy.id": "p1"}, {}),
            ("shell.command.completed", {"shell.command": "npm run test"}, {}),
            ("file.modified", {"file.path": "/etc/passwd"}, {}),
            ("tool.call.completed", {"tool.name": "shell"}, {}),
            ("run.completed", {}, {}),
        ],
    ),
    "multi": (
        4,
        [
            ("run.started", {"run.name": "Plan work"}, {"agent_id": "planner"}),
            ("tool.call.completed", {"tool.name": "github"}, {"agent_id": "coder"}),
            ("db.query.completed", {"db.operation": "INSERT"}, {"agent_id": "coder"}),
            ("run.completed", {}, {"agent_id": "planner"}),
        ],
    ),
    "unindexed": (
        5,
        [
            ("run.started", {"run.name": "Old one"}, {}),
            ("tool.call.completed", {"tool.name": "github"}, {}),
            ("run.completed", {}, {}),
        ],
    ),
}


@pytest.fixture
async def seeded(engine: AsyncEngine, database_url: str) -> AsyncIterator[Seeded]:
    tenant = await make_tenant(engine, "acme")
    ids: dict[str, uuid.UUID] = {}
    for name, (days_ago, spec) in SEEDS.items():
        run = make_run_ids()
        ids[name] = to_uuid(run["run_id"])
        await ingest(engine, tenant, run_events(tenant, run, days_ago, spec))
    async with engine.begin() as conn:  # a queued run: created by POST /v1/runs, no events
        ids["queued"] = uuid.uuid4()
        await RunRepository(conn, tenant.context).create_queued(
            run_id=ids["queued"],
            project_id=tenant.project_uuids["p"],
            trace_id=uuid.uuid4(),
            name="Queued job",
            agent_slug=None,
            metadata={},
            now=T0 - timedelta(days=6),
        )
    await drain(worker_for(engine, database_url))
    async with engine.begin() as conn:  # a run summarised before the index existed
        await conn.execute(delete(t.run_search).where(t.run_search.c.run_id == ids["unindexed"]))
    yield Seeded(tenant, ids)


async def find(
    engine: AsyncEngine,
    seeded: Seeded,
    q: str,
    *,
    who: tuple[str, ...] = (actions.PAYLOAD_READ,),
    **filters: Any,
) -> set[str]:
    compiled = compile_query(parse(q, lambda: NOW), principal(seeded.tenant, *who))
    async with engine.connect() as conn:
        records = await RunQueries(conn, seeded.tenant.context).page(
            limit=100, search=compiled, **filters
        )
    return seeded.names(records)


# ------------------------------------------------------------------ the syntax assumption


async def test_postgres_accepts_the_literal_syntax_we_build(engine: AsyncEngine) -> None:
    """Pins the tsvector/tsquery input syntax (plan F1) against the real database."""
    async with engine.connect() as conn:

        async def matches(document: str, query: str) -> bool:
            row = await conn.execute(
                text("SELECT CAST(:d AS tsvector) @@ CAST(:q AS tsquery)"),
                {"d": document, "q": query},
            )
            return bool(row.scalar_one())

        assert await matches("'a':1 'b':2", "'a' <-> 'b'")
        assert not await matches("'a':1 'b':3", "'a' <-> 'b'")
        assert await matches("'src/auth.ts':7 'src':7 'auth.ts':7", "'src/au':*")
        assert not await matches(
            "'src':7 'auth':7", "'src' <-> 'auth'"
        )  # same position: not adjacent
        assert await matches("'it''s':1 'a\\\\b':2", "'it''s' & 'a\\\\b'")
        assert not await matches("'foo_bar':1", "'bar'")  # no re-parsing of foo_bar
        assert await matches("'foo_bar':1", "'foo_b':*")


# ------------------------------------------------------------------ fields


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("status:failed", {"fail"}),
        ("status:success", {"ok", "secure", "multi", "unindexed"}),
        ("status:running", {"running"}),
        ("status:queued", {"queued"}),
        ("-status:success", {"fail", "running", "queued"}),
        ("agent:planner", {"multi"}),
        ("agent:coding-agent", {"fail", "ok", "running", "secure"}),
        ("agent:cod*", {"fail", "ok", "running", "secure", "multi"}),
        ("root_agent:planner", {"multi"}),
        ("root_agent:coding-agent", {"fail", "ok", "running", "secure", "unindexed"}),
        ("name:deploy", {"ok"}),
        ("name:DEPLOY", {"ok"}),
        ("-name:deploy", {"fail", "multi", "unindexed", "queued"}),  # unnamed runs never match
        ("cost:>1", {"ok"}),
        ("cost:>=0.5", {"fail", "ok"}),
        ("cost:0.5", {"fail"}),
        ("cost:<0.6", {"fail", "running", "secure", "multi", "unindexed", "queued"}),
        ("retries:>=2", {"fail"}),
        ("retries:0", {"ok", "running", "secure", "multi", "unindexed", "queued"}),
        ("errors:>0", {"fail"}),
        ("severity:error", {"fail"}),
        ("-errors:>0", {"ok", "running", "secure", "multi"}),
        ("duration:>0s", {"fail", "ok", "secure", "multi", "unindexed"}),
        ("-duration:>1h", {"fail", "ok", "secure", "multi", "unindexed"}),  # running: null
        ("started:2026-10-07", {"fail"}),
        ("started:2026-10-06", {"ok"}),
        ("started:>=2026-10-05", {"fail", "ok", "running"}),
        ("started:<2026-10-04", {"multi", "unindexed", "queued"}),
        ("started:>=-2d", {"fail", "ok"}),
        ("event:shell.command.completed", {"fail", "ok", "secure"}),
        ("event:shell.*", {"fail", "ok", "secure"}),
        ("event:db.*", {"multi"}),
        ("tool:github", {"fail", "running", "multi"}),
        ("tool:GITHUB", {"fail", "running", "multi"}),
        ("-tool:github", {"ok", "secure"}),
        ("tool:git*", {"fail", "running", "multi"}),
        ("model:m1", {"fail", "ok"}),
        ("provider:prov", {"fail", "ok"}),
        ("tag:prod", {"fail"}),
        ("security:true", {"secure"}),
        ("security:false", {"fail", "ok", "running", "multi"}),
        ("tool:github status:failed", {"fail"}),
        ("tool:github -status:failed", {"running", "multi"}),
        ("file:src/auth/**", {"ok"}),
        ("file:*.ts", {"ok"}),
        ("file:/etc/passwd", {"secure"}),
        ("file:/etc", set()),  # an absolute path matches exactly
        ("file:passwd", {"secure"}),
        ("-file:/etc/passwd", {"fail", "ok", "running", "multi"}),
        ("command:curl", {"fail"}),
        ("command:NPM", {"ok", "secure"}),
        ("command:npm -command:run", {"ok"}),
    ],
)
async def test_each_field_returns_exactly_the_expected_runs(
    engine: AsyncEngine, seeded: Seeded, q: str, expected: set[str]
) -> None:
    got = await find(engine, seeded, q)
    # `unindexed` and `queued` have no document: index terms never match them (D9), row terms do.
    assert got == expected - ({"unindexed", "queued"} if _needs_index(q) else set()), q


def _needs_index(q: str) -> bool:
    who = Principal(
        kind="user",
        workspace_id=uuid.uuid4(),
        project_id=None,
        actions=frozenset({actions.RUN_READ, actions.PAYLOAD_READ}),
        actor_id="user:test",
    )
    return compile_query(parse(q, lambda: NOW), who).needs_index


# ------------------------------------------------------------------ text


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("deploy", {"ok"}),
        ("DEPLOY api", {"ok"}),
        ('"deploy api"', {"ok"}),
        ('"api deploy"', set()),
        ("dep*", {"ok"}),
        ("github", {"fail", "running", "multi"}),
        ("-github", {"ok", "secure"}),
        ("insert", {"multi"}),
        ('"npm test"', {"ok"}),  # phrase: adjacent, not "npm run test"
        ("npm test", {"ok", "secure"}),
        ("src/auth.test.ts", {"ok"}),
        ("auth", {"ok"}),
        ("src/au*", {"ok"}),
        ("s3cr3t", {"fail"}),
        ('"bearer s3cr3t"', {"fail"}),
        ("passwd", {"secure"}),
        ("login", {"fail", "ok"}),
        ("nosuchword", set()),
        ("-nosuchword", {"fail", "ok", "running", "secure", "multi"}),
    ],
)
async def test_free_text_reads_metadata_and_content_for_content_readers(
    engine: AsyncEngine, seeded: Seeded, q: str, expected: set[str]
) -> None:
    assert await find(engine, seeded, q) == expected, q


@pytest.mark.parametrize("q", ["s3cr3t", "curl", "passwd", "auth", '"npm test"', "src/au*"])
async def test_a_viewer_sees_no_difference_between_content_words_and_nonsense(
    engine: AsyncEngine, seeded: Seeded, q: str
) -> None:
    viewer = ()
    nonsense = await find(engine, seeded, "nosuchword", who=viewer)
    assert await find(engine, seeded, q, who=viewer) == nonsense == set()
    assert await find(engine, seeded, f"-{q}", who=viewer) == await find(
        engine, seeded, "-nosuchword", who=viewer
    )


# ------------------------------------------------------------------ indexing gaps, facets, paging


async def test_unindexed_runs_match_no_index_term_but_do_match_row_terms(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    assert "unindexed" not in await find(engine, seeded, "tool:github")
    assert "unindexed" not in await find(engine, seeded, "-tool:nothing")  # not even negated
    assert "unindexed" in await find(engine, seeded, "status:success")
    assert "queued" in await find(engine, seeded, "name:queued")


async def facets(engine: AsyncEngine, seeded: Seeded, q: str, **kw: Any) -> Any:
    compiled = compile_query(parse(q, lambda: NOW), principal(seeded.tenant))
    async with engine.connect() as conn:
        return await RunQueries(conn, seeded.tenant.context).facets_and_unindexed(
            compiled, budget=SearchBudget(5.0), **kw
        )


async def test_facets_count_metadata_and_the_unindexed_count_rides_along(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    result = await facets(engine, seeded, "tool:github")
    assert dict(result.facets["status"]) == {"FAILED": 1, "RUNNING": 1, "SUCCESS": 1}
    assert dict(result.facets["tools"])["github"] == 3
    assert dict(result.facets["agents"])["coder"] == 1
    assert result.unindexed == 2 and not result.unindexed_capped  # queued + the deleted document
    assert all(len(v) <= 10 for v in result.facets.values())

    narrowed = await facets(engine, seeded, "tool:github status:failed")
    assert narrowed.unindexed == 0  # the row terms are applied to the count
    assert dict(narrowed.facets["status"]) == {"FAILED": 1}


async def test_a_run_row_only_query_counts_every_run_in_the_status_facet(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    result = await facets(engine, seeded, "cost:>=0")
    assert sum(n for _, n in result.facets["status"]) == len(seeded.ids)
    assert result.unindexed is None  # it does not need the index


async def test_facets_are_computed_over_at_most_1000_matches_and_10_values(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO runs (workspace_id, id, project_id, trace_id, status, started_at) "
                "SELECT :w, gen_random_uuid(), :p, gen_random_uuid(), 'SUCCESS', "
                "       now() - (g || ' seconds')::interval FROM generate_series(1, 1200) g"
            ),
            {"w": tenant.context.workspace_id, "p": tenant.project_uuids["p"]},
        )
        await conn.execute(
            text(
                "INSERT INTO run_search (workspace_id, run_id, project_id, started_at, "
                "  summary_version, document_hash, tools) "
                "SELECT workspace_id, id, project_id, started_at, 1, '\\x00', "
                "       ARRAY['tool-' || (row_number() OVER () % 15)] FROM runs"
            )
        )
    unfiltered = compile_query(parse("-tool:nothing", lambda: NOW), principal(tenant))
    async with engine.connect() as conn:
        result = await RunQueries(conn, tenant.context).facets_and_unindexed(
            unfiltered, budget=SearchBudget(5.0)
        )
    assert sum(n for _, n in result.facets["status"]) == 1000
    assert len(result.facets["tools"]) == 10
    assert sum(n for _, n in result.facets["tools"]) <= 1000


async def test_keyset_paging_through_a_search_has_no_gaps_or_repeats(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    tenant = seeded.tenant
    compiled = compile_query(parse("-tool:nosuchtool", lambda: NOW), principal(tenant))
    expected = seeded.names([r for r in await _all(engine, tenant, compiled, 100)])
    seen: list[uuid.UUID] = []
    after = None
    for _ in range(10):
        async with engine.connect() as conn:
            page = await RunQueries(conn, tenant.context).page(
                limit=3, search=compiled, after=after
            )
        if not page:
            break
        seen += [r.id for r in page]
        after = (page[-1].started_at, page[-1].id)
    assert len(seen) == len(set(seen)) and {seeded.by_id[i] for i in seen} == expected
    assert len(seen) == 5  # the indexed runs: queued and the deleted document are absent


async def _all(engine: AsyncEngine, tenant: Tenant, compiled: Any, limit: int) -> list[Any]:
    async with engine.connect() as conn:
        return await RunQueries(conn, tenant.context).page(limit=limit, search=compiled)


async def test_a_page_carries_matched_arrays_and_flags(engine: AsyncEngine, seeded: Seeded) -> None:
    compiled = compile_query(parse("tool:github model:m*", lambda: NOW), principal(seeded.tenant))
    async with engine.connect() as conn:
        records = await RunQueries(conn, seeded.tenant.context).page(limit=10, search=compiled)
    fail = next(r for r in records if r.id == seeded.ids["fail"])
    assert fail.search is not None and fail.search.content_truncated is None
    assert compiled.matched(fail.search.arrays) == {"tools": ["github"], "models": ["m1"]}

    owner = compile_query(
        parse("tool:github", lambda: NOW), principal(seeded.tenant, actions.PAYLOAD_READ)
    )
    async with engine.connect() as conn:
        records = await RunQueries(conn, seeded.tenant.context).page(limit=10, search=owner)
    assert records[0].search is not None and records[0].search.content_truncated is False


async def test_the_search_is_tenant_scoped(engine: AsyncEngine, seeded: Seeded) -> None:
    other = await make_tenant(engine, "globex")
    compiled = compile_query(parse("tool:github", lambda: NOW), principal(other))
    async with engine.connect() as conn:
        assert await RunQueries(conn, other.context).page(limit=10, search=compiled) == []
        result = await RunQueries(conn, other.context).facets_and_unindexed(
            compiled, budget=SearchBudget(5.0)
        )
    assert result.unindexed == 0 and all(not v for v in result.facets.values())


async def test_a_project_bound_scope_restricts_the_search(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    compiled = compile_query(parse("tool:github", lambda: NOW), principal(seeded.tenant))
    async with engine.connect() as conn:
        queries = RunQueries(conn, seeded.tenant.context, uuid.uuid4())  # another project
        assert await queries.page(limit=10, search=compiled) == []
        facet = await queries.facets_and_unindexed(compiled, budget=SearchBudget(5.0))
    assert facet.unindexed == 0 and dict(facet.facets["status"]) == {}


async def test_the_legacy_filters_and_a_query_are_anded(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    got = await find(engine, seeded, "tool:github", statuses=["FAILED"])
    assert got == {"fail"}
    got = await find(engine, seeded, "tool:github", agent_slug="planner")
    assert got == {"multi"}  # the first agent of that run
    assert await find(engine, seeded, "tool:github", agent_slug="coder") == set()
    assert await find(engine, seeded, "tool:github", statuses=["SUCCESS"]) == {"multi"}


async def test_agent_ids_with_dots_and_underscores_are_searchable(
    engine: AsyncEngine, database_url: str
) -> None:
    """Review F2: the agent id rule of the schema and of `?agent_id=` is also the `agent:` rule."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    spec = [
        ("run.started", {"run.name": "Dotted"}, {"agent_id": "my_agent"}),
        ("tool.call.completed", {"tool.name": "github"}, {"agent_id": "v1.2-bot"}),
        ("run.completed", {}, {"agent_id": "my_agent"}),
    ]
    await ingest(engine, tenant, run_events(tenant, run, 0, spec))
    await drain(worker_for(engine, database_url))
    world = Seeded(tenant, {"dotted": to_uuid(run["run_id"])})
    assert await find(engine, world, "agent:my_agent") == {"dotted"}
    assert await find(engine, world, "agent:v1.2-bot") == {"dotted"}
    assert await find(engine, world, "agent:v1.*") == {"dotted"}
    assert await find(engine, world, "root_agent:my_agent") == {"dotted"}
    assert await find(engine, world, "agent:my_agent", agent_slug="my_agent") == {"dotted"}
    assert await find(engine, world, "agent:my_other") == set()


# ------------------------------------------------------------------ budget and gate


async def test_a_statement_past_its_budget_is_a_retryable_timeout(engine: AsyncEngine) -> None:
    from abb_api.core.errors import AppError

    async with engine.connect() as conn:
        with pytest.raises(AppError) as caught:
            await SearchBudget(0.05).run(conn, text("SELECT pg_sleep(2)"))
    assert caught.value.code == "SEARCH_TIMEOUT" and caught.value.retryable
    assert caught.value.status_code == 503


async def test_the_budget_makes_the_transaction_read_only(engine: AsyncEngine) -> None:
    from sqlalchemy.exc import DBAPIError

    async with engine.connect() as conn:
        budget = SearchBudget(5.0)
        await budget.run(conn, text("SELECT 1"))
        with pytest.raises(DBAPIError, match="read-only"):
            await conn.execute(text("CREATE TEMP TABLE nope (x int)"))


async def test_facets_are_skipped_when_less_than_forty_percent_of_the_budget_remains() -> None:
    now = [0.0]
    budget = SearchBudget(10.0, clock=lambda: now[0])
    assert budget.allows_facets()
    now[0] = 6.1
    assert not budget.allows_facets()
    now[0] = 11.0
    assert budget.remaining_seconds() == 0.0


async def test_the_gate_refuses_instead_of_queueing() -> None:
    import asyncio

    from abb_api.core.errors import AppError

    gate = SearchGate(max_concurrent=2, timeout_seconds=1.0)
    release = asyncio.Event()
    entered = 0

    async def hold() -> None:
        nonlocal entered
        async with gate.request():
            entered += 1
            await release.wait()

    first, second = asyncio.create_task(hold()), asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    assert entered == 2
    with pytest.raises(AppError) as caught:
        async with gate.request():
            raise AssertionError("must not be admitted")
    assert caught.value.code == "SEARCH_BUSY" and caught.value.headers == {"Retry-After": "1"}
    release.set()
    await asyncio.gather(first, second)
    async with gate.request():  # permits come back
        pass
