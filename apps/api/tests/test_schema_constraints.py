"""The database enforces the invariants itself, so application bugs cannot break them."""

import hashlib
from typing import Any

import pytest
from sqlalchemy import Table, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.db import tables as t
from tests.factories import (
    NOW,
    make_event,
    make_project,
    make_run,
    make_workspace,
    uid,
)


async def count(conn: AsyncConnection, table: Table) -> int:
    return int((await conn.execute(select(func.count()).select_from(table))).scalar_one())


# ------------------------------------------------------------------ tenant isolation


async def test_two_workspaces_may_use_the_same_ids(engine: AsyncEngine) -> None:
    """Primary keys are (workspace_id, id): tenants cannot collide on or probe each other."""
    shared_project, shared_run, shared_event = uid(), uid(), uid()
    async with engine.begin() as conn:
        for _ in range(2):
            ws = await make_workspace(conn)
            await make_project(conn, ws, "p", shared_project)
            await make_run(conn, ws, shared_project, shared_run)
            await make_event(conn, ws, shared_project, shared_run, shared_event)
        assert await count(conn, t.events) == 2


async def test_a_child_cannot_reference_another_tenants_parent(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws_a, ws_b = await make_workspace(conn), await make_workspace(conn)
        project_a = await make_project(conn, ws_a)
        await make_project(conn, ws_b)
    # run in B pointing at A's project
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await make_run(conn, ws_b, project_a)
    # agent in B pointing at A's project
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.agents).values(workspace_id=ws_b, id=uid(), project_id=project_a, slug="x")
            )
    # api key of B scoped to A's project
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.api_keys).values(
                    id=uid(), key_id="k1", workspace_id=ws_b, project_id=project_a,
                    secret_hash=b"x" * 32, scopes=["runs:read"],
                )
            )  # fmt: skip


async def test_an_event_cannot_attach_to_another_tenants_run(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws_a, ws_b = await make_workspace(conn), await make_workspace(conn)
        project_a, project_b = await make_project(conn, ws_a), await make_project(conn, ws_b)
        run_a = await make_run(conn, ws_a, project_a)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await make_event(conn, ws_b, project_b, run_a)


# ------------------------------------------------------------------ idempotency and immutability


async def test_event_identity_is_workspace_and_event_id(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        project = await make_project(conn, ws)
        run = await make_run(conn, ws, project)
        event_id = await make_event(conn, ws, project, run)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await make_event(conn, ws, project, run, event_id)


async def test_insert_on_conflict_do_nothing_is_the_idempotent_write(engine: AsyncEngine) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        project = await make_project(conn, ws)
        run = await make_run(conn, ws, project)
        event_id = await make_event(conn, ws, project, run)
        statement = (
            pg_insert(t.events)
            .values(
                workspace_id=ws, event_id=event_id, project_id=project, run_id=run, trace_id=uid(),
                agent_id="a", event_type="run.started", occurred_at=NOW, schema_version="1.0",
                content_hash=hashlib.sha256(b"different").digest(),
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "event_id"])
            .returning(t.events.c.event_id)
        )  # fmt: skip
        assert (await conn.execute(statement)).first() is None
        original = (await conn.execute(select(t.events.c.content_hash))).scalar_one()
        assert original == hashlib.sha256(event_id.bytes).digest()  # first write wins


async def test_a_run_with_events_cannot_be_deleted_until_retention_removes_them(
    engine: AsyncEngine,
) -> None:
    """INV-1 (0008): events are never a side effect of deleting a run; derived spans still cascade."""
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        project = await make_project(conn, ws)
        run = await make_run(conn, ws, project)
        await make_event(conn, ws, project, run)
        await conn.execute(
            insert(t.spans).values(
                workspace_id=ws, id=uid(), run_id=run, trace_id=uid(), agent_slug="a"
            )
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(delete(t.runs).where(t.runs.c.id == run))
    async with engine.begin() as conn:  # the explicit, privileged retention path
        await conn.execute(text("DELETE FROM events"))
        await conn.execute(delete(t.runs).where(t.runs.c.id == run))
        assert await count(conn, t.spans) == 0


# ------------------------------------------------------------------ value constraints


@pytest.mark.parametrize(("column", "bad"), [("status", "DONE"), ("ordering_mode", "random")])
async def test_run_enums_are_checked(engine: AsyncEngine, column: str, bad: str) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        project = await make_project(conn, ws)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            overrides: dict[str, Any] = {column: bad}
            await make_run(conn, ws, project, **overrides)


async def test_api_key_scopes_are_checked(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        await conn.execute(
            insert(t.api_keys).values(
                id=uid(), key_id="ok", workspace_id=ws, secret_hash=b"x" * 32,
                scopes=["events:write", "runs:read"],
            )
        )  # fmt: skip
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.api_keys).values(
                    id=uid(), key_id="bad", workspace_id=ws, secret_hash=b"x" * 32,
                    scopes=["admin:everything"],
                )
            )  # fmt: skip


async def test_key_ids_are_globally_unique(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws_a, ws_b = await make_workspace(conn), await make_workspace(conn)
        await conn.execute(
            insert(t.api_keys).values(
                id=uid(), key_id="same", workspace_id=ws_a, secret_hash=b"x", scopes=["runs:read"]
            )
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.api_keys).values(
                    id=uid(), key_id="same", workspace_id=ws_b, secret_hash=b"x", scopes=[]
                )
            )


async def test_member_roles_and_user_emails(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        user = uid()
        await conn.execute(insert(t.users).values(id=user, email="Dev@Example.com"))
        await conn.execute(
            insert(t.workspace_members).values(workspace_id=ws, user_id=user, role="BILLING")
        )
    with pytest.raises(IntegrityError):  # email uniqueness is case-insensitive
        async with engine.begin() as conn:
            await conn.execute(insert(t.users).values(id=uid(), email="dev@example.com"))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            other = uid()
            await conn.execute(insert(t.users).values(id=other, email="o@example.com"))
            await conn.execute(
                insert(t.workspace_members).values(workspace_id=ws, user_id=other, role="GOD")
            )


async def test_project_slug_is_unique_per_workspace_only(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws_a, ws_b = await make_workspace(conn), await make_workspace(conn)
        await make_project(conn, ws_a, "coding-agent")
        await make_project(conn, ws_b, "coding-agent")  # fine: different workspace
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await make_project(conn, ws_a, "coding-agent")


# ------------------------------------------------------------------ outbox


async def test_only_one_pending_job_per_dedupe_key(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)

        def job(status: str = "pending") -> dict[str, object]:
            return {
                "id": uid(), "job_type": "summarize_run", "workspace_id": ws,
                "dedupe_key": f"{ws}:run-1", "status": status,
            }  # fmt: skip

        await conn.execute(insert(t.outbox_jobs).values(**job()))
        # a running or finished job does not block a new pending one (new events arrive mid-run)
        await conn.execute(insert(t.outbox_jobs).values(**job("running")))
        await conn.execute(insert(t.outbox_jobs).values(**job("done")))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.outbox_jobs).values(**job()))
    async with engine.begin() as conn:
        await conn.execute(
            update(t.outbox_jobs).where(t.outbox_jobs.c.status == "pending").values(status="done")
        )
        await conn.execute(insert(t.outbox_jobs).values(**job()))


async def test_outbox_status_is_checked_and_policy_action_too(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.outbox_jobs).values(
                    id=uid(), job_type="x", workspace_id=ws, dedupe_key="k", status="weird"
                )
            )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.policies).values(
                    workspace_id=ws, id=uid(), name="p", rule={}, action="maybe"
                )
            )


async def test_json_nul_cannot_be_stored_so_validation_must_keep_it_out(
    engine: AsyncEngine,
) -> None:
    """Why the event contract rejects U+0000: PostgreSQL itself refuses it in jsonb."""
    async with engine.begin() as conn:
        with pytest.raises(DBAPIError, match=r"u0000|unsupported Unicode"):
            await conn.execute(text('SELECT CAST(\'{"k": "a\\u0000b"}\' AS jsonb)'))
