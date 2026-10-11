"""Seeding and statement capture for the search tests that go through HTTP."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete
from sqlalchemy import event as sa_event

from abb_api.db import tables as t
from tests.api_fixtures import Api
from tests.ingest_helpers import T0, make_run_ids, wire_event

Spec = list[tuple[str, dict[str, Any], dict[str, Any]]]


def stamp(days_ago: int, n: int) -> str:
    moment = T0 - timedelta(days=days_ago) + timedelta(seconds=n)
    return moment.isoformat().replace("+00:00", "Z")


async def ingest_spec(
    api: Api, spec: Spec, days_ago: int = 0, *, token: str = "writer"
) -> dict[str, str]:
    """One run from `spec` through the public ingestion API (so documents are production's)."""
    run = make_run_ids()
    events = [
        wire_event(
            run, n, event_type=kind, attributes=attrs, occurred_at=stamp(days_ago, n), **extra
        )
        for n, (kind, attrs, extra) in enumerate(spec, start=1)
    ]
    response = await api.post_batch(events, token=token)
    assert response.status_code == 202, response.text
    return run


async def seed_named(
    api: Api, seeds: dict[str, tuple[int, Spec]], *, drop_document_of: str | None = None
) -> dict[str, str]:
    """Run ids by seed name; optionally one run loses its document (as if summarised pre-index)."""
    ids = {
        name: (await ingest_spec(api, spec, days))["run_id"] for name, (days, spec) in seeds.items()
    }
    await api.drain()
    if drop_document_of is not None:
        from abb_event_schema.ids import to_uuid

        async with api.engine.begin() as conn:
            await conn.execute(
                delete(t.run_search).where(t.run_search.c.run_id == to_uuid(ids[drop_document_of]))
            )
    return ids


def item_ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@contextmanager
def statements(api: Api) -> Iterator[list[str]]:
    """Every SQL statement the application's own engine executes inside the block."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        seen.append(statement)

    engine = api.app.state.engine.sync_engine
    sa_event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        sa_event.remove(engine, "before_cursor_execute", record)


def mentioning(seen: list[str], table: str) -> list[str]:
    return [s for s in seen if re.search(rf"\b{table}\b", s)]
