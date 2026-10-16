"""A row that arrives after a purge's recorded drain step is removed, not a wedge (review P1-1).

Reproduced by a hook that inserts the row at the point named after the drain committed."""

import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.retention import purge
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import make_store, scalar, truncate_all
from tests.retention.purge_helpers import (
    seed_destination,
    seed_project_world,
    seed_secret,
    seed_user,
    seed_workspace_extras,
)
from tests.retention.test_purge import project_request, run, workspace_request


async def test_workspace_purge_removes_a_job_that_arrives_after_the_drain(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    await truncate_all(engine)
    store = make_store(tmp_path)
    key = await seed_secret(engine)
    acme = await make_tenant(engine, "acme", projects=("p1",))
    destination = await seed_destination(engine, acme, key)
    await seed_project_world(engine, acme, "p1", store, runs=1, destination=destination)
    await seed_workspace_extras(engine, acme, key)
    await seed_user(engine, "sole@x.test", member_of=[(acme, "OWNER")])
    ws = acme.context.workspace_id

    async def late_job(point: str) -> None:
        if point == "purge:workspace_rows":
            async with engine.begin() as conn:  # the worker, as the owner here
                await conn.execute(
                    text(
                        "INSERT INTO outbox_jobs (id, job_type, workspace_id, dedupe_key) "
                        "VALUES (gen_random_uuid(), 'analytics_refresh', :w, :k)"
                    ),
                    {"w": ws, "k": f"analytics:{ws}:late"},
                )

    code, lines = await run(
        retention_engine, store, workspace_request(confirm="acme"), hook=late_job
    )
    assert code == purge.EXIT_OK, lines
    assert await scalar(engine, "SELECT count(*) FROM workspaces WHERE id = :w", w=ws) == 0
    assert (
        await scalar(engine, "SELECT count(*) FROM outbox_jobs WHERE workspace_id = :w", w=ws) == 0
    )


async def test_project_purge_removes_a_row_that_arrives_after_the_drain(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    await truncate_all(engine)
    store = make_store(tmp_path)
    key = await seed_secret(engine)
    acme = await make_tenant(engine, "acme", projects=("p1",))
    destination = await seed_destination(engine, acme, key)
    world = await seed_project_world(engine, acme, "p1", store, runs=1, destination=destination)
    ws = acme.context.workspace_id
    project_id = world.project_id

    async def late_row(point: str) -> None:
        if point == "purge:api_keys":
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO pricing_overrides (workspace_id, id, project_id, "
                        "model_pattern, input_per_million, output_per_million, valid_from) "
                        "VALUES (:w, gen_random_uuid(), :p, 'late', 1, 1, now())"
                    ),
                    {"w": ws, "p": project_id},
                )

    code, lines = await run(retention_engine, store, project_request(confirm="p1"), hook=late_row)
    assert code == purge.EXIT_OK, lines
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE id = :p", p=project_id) == 0
    assert (
        await scalar(
            engine, "SELECT count(*) FROM pricing_overrides WHERE project_id = :p", p=project_id
        )
        == 0
    )
