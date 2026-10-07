"""Builders for ingestion tests: a tenant with projects, and canonical events bound to it."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind, from_uuid, new_id
from abb_event_schema.parse import parse_event_in
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces import WorkspaceProvisioning

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 10, 7, 12, 5, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Tenant:
    context: TenantContext
    workspace_id: str  # public ids
    projects: dict[str, str]  # slug -> public project id
    project_uuids: dict[str, uuid.UUID]


async def make_tenant(engine: AsyncEngine, slug: str, projects: tuple[str, ...] = ("p",)) -> Tenant:
    async with engine.begin() as conn:
        workspace = await WorkspaceProvisioning(conn).create(name=slug, slug=slug)
        context = TenantContext(workspace.id)
        created = {
            p: await ProjectRepository(conn, context).create(name=p, slug=p) for p in projects
        }
    return Tenant(
        context=context,
        workspace_id=from_uuid(IdKind.WORKSPACE, workspace.id),
        projects={p: from_uuid(IdKind.PROJECT, c.id) for p, c in created.items()},
        project_uuids={p: c.id for p, c in created.items()},
    )


def make_run_ids() -> dict[str, str]:
    return {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}


def build_event(
    tenant: Tenant,
    run: dict[str, str],
    *,
    n: int,
    project: str = "p",
    event_id: str | None = None,
    event_type: str = "tool.call.completed",
    attributes: dict[str, Any] | None = None,
    **overrides: Any,
) -> Event:
    """A finalized event number `n` of a run (sequence n, one second apart)."""
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": event_id or new_id(IdKind.EVENT),
        "run_id": run["run_id"],
        "trace_id": run["trace_id"],
        "span_id": new_id(IdKind.SPAN),
        "agent_id": "coding-agent",
        "event_type": event_type,
        "occurred_at": (T0 + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
        "sequence": n,
        "attributes": attributes if attributes is not None else {"tool.name": "github"},
    }
    raw.update(overrides)
    return finalize(
        parse_event_in({k: v for k, v in raw.items() if v is not ...}),
        workspace_id=tenant.workspace_id,
        project_id=tenant.projects[project],
        received_at=RECEIVED,
    )
