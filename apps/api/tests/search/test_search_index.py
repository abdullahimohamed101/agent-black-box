"""The summarizer writes the search document; it is skipped when unchanged and rebuildable."""

import io
from typing import Any

from abb_event_schema.ids import to_uuid
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api import cli
from abb_api.db import tables as t
from abb_api.jobs.outbox import OutboxRepository
from abb_api.runs.summary import SUMMARY_VERSION, derive_run
from abb_api.search.document import build_document, distinct_agents
from abb_api.search.repository import RunSearchRepository
from tests.conftest import make_settings
from tests.ingest_helpers import build_event, make_run_ids, make_tenant
from tests.search.test_document import COMMAND, PATH
from tests.test_summarizer import drain, ingest, worker_for


async def search_row(engine: AsyncEngine, run_id: str) -> Any:
    async with engine.connect() as conn:
        return (
            await conn.execute(select(t.run_search).where(t.run_search.c.run_id == to_uuid(run_id)))
        ).first()


def events_for(tenant: Any, run: dict[str, str]) -> list[Any]:
    def ev(n: int, kind: str, attributes: dict[str, Any] | None = None) -> Any:
        return build_event(tenant, run, n=n, event_type=kind, attributes=attributes or {})

    return [
        ev(1, "run.started", {"run.name": "Index me"}),
        ev(2, "tool.call.completed", {"tool.name": "GitHub"}),
        ev(3, "shell.command.completed", {"shell.command": COMMAND}),
        ev(4, "file.modified", {"file.path": PATH}),
        ev(5, "run.completed"),
    ]


async def invoke_cli(settings: Any, *argv: str) -> tuple[int, str]:
    err = io.StringIO()
    code = await cli.run(list(argv), settings, out=io.StringIO(), err=err)
    return code, err.getvalue()


async def test_the_summarizer_writes_the_document_and_it_is_rebuildable(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = events_for(tenant, run)
    await ingest(engine, tenant, events)
    assert await search_row(engine, run["run_id"]) is None  # not summarised yet
    await drain(worker_for(engine, database_url))

    row = await search_row(engine, run["run_id"])
    assert (
        row.summary_version == SUMMARY_VERSION and row.workspace_id == tenant.context.workspace_id
    )
    assert row.tools == ["github"] and row.commands == [COMMAND] and row.file_paths == [PATH]
    derived = derive_run(events)
    expected = build_document(
        events,
        run_name=derived.name,
        run_metadata={},
        agents=distinct_agents(events),
        error_count=derived.summary["error_count"],
    )
    assert (
        row.error_count == expected.error_count and tuple(row.event_types) == expected.event_types
    )
    async with engine.connect() as conn:
        literal = (
            await conn.execute(
                text("SELECT metadata_doc::text AS m, content_doc::text AS c FROM run_search")
            )
        ).one()
    assert "'github'" in literal.m and "'index'" in literal.m
    assert "'cmdcanary-curl'" in literal.c and "'id_rsa'" in literal.c
    assert "cmdcanary" not in literal.m  # content never reaches the metadata document


async def test_an_unchanged_resummarisation_writes_nothing_and_a_change_does(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = events_for(tenant, run)
    await ingest(engine, tenant, events)
    worker = worker_for(engine, database_url)
    await drain(worker)
    first = await search_row(engine, run["run_id"])

    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue_summarize([to_uuid(run["run_id"])])
    await drain(worker)
    again = await search_row(engine, run["run_id"])
    assert again.updated_at == first.updated_at and again.document_hash == first.document_hash

    await ingest(
        engine,
        tenant,
        [
            build_event(
                tenant,
                run,
                n=6,
                event_type="tool.call.completed",
                attributes={"tool.name": "slack"},
            )
        ],
    )
    await drain(worker)
    changed = await search_row(engine, run["run_id"])
    assert changed.tools == ["github", "slack"] and changed.document_hash != first.document_hash

    async with engine.begin() as conn:
        document = build_document(
            events,
            run_name="Index me",
            run_metadata={},
            agents=distinct_agents(events),
            error_count=0,
        )
        repository = RunSearchRepository(conn, tenant.context)
        written = await repository.upsert_if_changed(
            run_id=to_uuid(run["run_id"]),
            project_id=changed.project_id,
            started_at=changed.started_at,
            version=SUMMARY_VERSION,
            document=document,
        )
        assert written is True  # a different document than the stored one
        again = await repository.upsert_if_changed(
            run_id=to_uuid(run["run_id"]),
            project_id=changed.project_id,
            started_at=changed.started_at,
            version=SUMMARY_VERSION,
            document=document,
        )
        assert again is False


async def test_search_rebuild_restores_missing_documents_and_is_audited(
    engine: AsyncEngine, database_url: str
) -> None:
    settings = make_settings(database_url)
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, events_for(tenant, run))
    worker = worker_for(engine, database_url)
    await drain(worker)
    before = await search_row(engine, run["run_id"])

    async with engine.begin() as conn:
        await conn.execute(delete(t.run_search))
    assert await search_row(engine, run["run_id"]) is None

    code, err = await invoke_cli(settings, "search-rebuild", "--workspace", "acme")
    assert code == 0 and "queued 1 of 1" in err
    await drain(worker)
    after = await search_row(engine, run["run_id"])
    assert after is not None and after.document_hash == before.document_hash
    assert after.commands == before.commands and after.tools == before.tools

    code, err = await invoke_cli(settings, "search-rebuild", "--workspace", "acme")
    assert code == 0 and "queued 0 of 0" in err  # nothing left to index

    async with engine.connect() as conn:
        audit = (
            await conn.execute(
                text("SELECT action, actor_kind, details FROM audit_log ORDER BY id")
            )
        ).all()
    assert [a.action for a in audit] == ["search.rebuild", "search.rebuild"]
    assert audit[0].actor_kind == "cli" and audit[0].details == {"queued": 1, "matched": 1}
    assert "CMDCANARY" not in str(audit)


async def test_unindexed_runs_exclude_queued_and_unsummarised_ones(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, events_for(tenant, run))  # a run row, version 0
    async with engine.connect() as conn:
        repository = RunSearchRepository(conn, tenant.context)
        assert (
            await repository.unindexed_run_ids(
                project_id=None, since=None, version=SUMMARY_VERSION, limit=10
            )
            == []
        )
    await drain(worker_for(engine, database_url))
    async with engine.begin() as conn:
        await conn.execute(delete(t.run_search))
    async with engine.connect() as conn:
        repository = RunSearchRepository(conn, tenant.context)
        found = await repository.unindexed_run_ids(
            project_id=None, since=None, version=SUMMARY_VERSION, limit=10
        )
        assert found == [to_uuid(run["run_id"])]
        other = await make_tenant(engine, "globex")
        assert (
            await RunSearchRepository(conn, other.context).unindexed_run_ids(
                project_id=None, since=None, version=SUMMARY_VERSION, limit=10
            )
            == []
        )
