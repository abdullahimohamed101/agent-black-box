"""Row builders for database tests. Plain Core inserts: no application code under test."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def uid() -> uuid.UUID:
    return uuid.uuid4()


async def make_workspace(conn: AsyncConnection, slug: str | None = None) -> uuid.UUID:
    workspace_id = uid()
    await conn.execute(
        insert(t.workspaces).values(id=workspace_id, name="W", slug=slug or f"ws-{workspace_id}")
    )
    return workspace_id


async def make_project(
    conn: AsyncConnection,
    workspace_id: uuid.UUID,
    slug: str = "p",
    project_id: uuid.UUID | None = None,
) -> uuid.UUID:
    project_id = project_id or uid()
    await conn.execute(
        insert(t.projects).values(workspace_id=workspace_id, id=project_id, name=slug, slug=slug)
    )
    return project_id


async def make_run(
    conn: AsyncConnection,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    **overrides: Any,
) -> uuid.UUID:
    run_id = run_id or uid()
    values: dict[str, Any] = {
        "workspace_id": workspace_id,
        "id": run_id,
        "project_id": project_id,
        "trace_id": uid(),
        "status": "RUNNING",
        "started_at": NOW,
    }
    values.update(overrides)
    await conn.execute(insert(t.runs).values(**values))
    return run_id


async def make_event(
    conn: AsyncConnection,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    event_id: uuid.UUID | None = None,
    **overrides: Any,
) -> uuid.UUID:
    event_id = event_id or uid()
    values: dict[str, Any] = {
        "workspace_id": workspace_id,
        "event_id": event_id,
        "project_id": project_id,
        "run_id": run_id,
        "trace_id": uid(),
        "agent_id": "coding-agent",
        "event_type": "run.started",
        "occurred_at": NOW,
        "schema_version": "1.0",
        "content_hash": hashlib.sha256(event_id.bytes).digest(),
    }
    values.update(overrides)
    await conn.execute(insert(t.events).values(**values))
    return event_id
