"""Projects and agents. Every statement is filtered by the tenant the repository was built for."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.domain import AlreadyExistsError
from abb_api.db import tables as t
from abb_api.ids import new_uuid
from abb_api.slugs import validate_slug
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class Project:
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


@dataclass(frozen=True)
class Agent:
    id: uuid.UUID
    project_id: uuid.UUID
    slug: str


class ProjectRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def create(self, *, name: str, slug: str) -> Project:
        validate_slug(slug, "project slug")
        statement = (
            insert(t.projects)
            .values(
                workspace_id=self._tenant.workspace_id,
                id=new_uuid(IdKind.PROJECT),
                name=name,
                slug=slug,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "slug"])
            .returning(
                t.projects.c.id, t.projects.c.name, t.projects.c.slug, t.projects.c.created_at
            )
        )
        row = (await self._conn.execute(statement)).first()
        if row is None:
            raise AlreadyExistsError(f"project '{slug}' already exists")
        return Project(row.id, row.name, row.slug, row.created_at)

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._one(t.projects.c.id == project_id)

    async def get_by_slug(self, slug: str) -> Project | None:
        return await self._one(t.projects.c.slug == slug)

    async def list(self) -> list[Project]:
        rows = await self._conn.execute(self._select().order_by(t.projects.c.slug))
        return [Project(r.id, r.name, r.slug, r.created_at) for r in rows]

    def _select(self) -> Select[Any]:
        return select(
            t.projects.c.id, t.projects.c.name, t.projects.c.slug, t.projects.c.created_at
        ).where(t.projects.c.workspace_id == self._tenant.workspace_id)

    async def _one(self, condition: ColumnElement[bool]) -> Project | None:
        row = (await self._conn.execute(self._select().where(condition))).first()
        return Project(row.id, row.name, row.slug, row.created_at) if row else None


class AgentRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def ensure(self, *, project_id: uuid.UUID, slug: str) -> Agent:
        """Return the agent for (project, slug), creating it on first sight. Race-safe."""
        await self._conn.execute(
            insert(t.agents)
            .values(
                workspace_id=self._tenant.workspace_id,
                id=uuid.uuid4(),
                project_id=project_id,
                slug=slug,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "project_id", "slug"])
        )
        row = (
            await self._conn.execute(
                select(t.agents.c.id).where(
                    t.agents.c.workspace_id == self._tenant.workspace_id,
                    t.agents.c.project_id == project_id,
                    t.agents.c.slug == slug,
                )
            )
        ).one()
        return Agent(row.id, project_id, slug)

    async def ensure_version(self, *, agent_id: uuid.UUID, fingerprint: str) -> uuid.UUID:
        await self._conn.execute(
            insert(t.agent_versions)
            .values(
                workspace_id=self._tenant.workspace_id,
                id=uuid.uuid4(),
                agent_id=agent_id,
                fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "agent_id", "fingerprint"])
        )
        row = (
            await self._conn.execute(
                select(t.agent_versions.c.id).where(
                    t.agent_versions.c.workspace_id == self._tenant.workspace_id,
                    t.agent_versions.c.agent_id == agent_id,
                    t.agent_versions.c.fingerprint == fingerprint,
                )
            )
        ).one()
        return uuid.UUID(str(row.id))
