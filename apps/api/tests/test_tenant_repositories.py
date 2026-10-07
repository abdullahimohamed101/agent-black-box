"""Projects, agents, workspaces: every method is scoped to the tenant it was built for."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.domain import AlreadyExistsError
from abb_api.db import tables as t
from abb_api.projects.repository import AgentRepository, ProjectRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces import WorkspaceProvisioning


async def two_workspaces(engine: AsyncEngine) -> tuple[TenantContext, TenantContext]:
    async with engine.begin() as conn:
        provisioning = WorkspaceProvisioning(conn)
        a = await provisioning.create(name="A", slug="acme")
        b = await provisioning.create(name="B", slug="globex")
    return TenantContext(a.id), TenantContext(b.id)


async def test_workspace_slugs_are_unique_and_validated(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await WorkspaceProvisioning(conn).create(name="A", slug="acme")
    async with engine.begin() as conn:
        with pytest.raises(AlreadyExistsError):
            await WorkspaceProvisioning(conn).create(name="A2", slug="acme")
        for bad in ["", "Acme", "has space", "-lead", "x" * 64, "a\n"]:
            with pytest.raises(ValueError, match="slug"):
                await WorkspaceProvisioning(conn).create(name="n", slug=bad)


async def test_projects_are_invisible_across_tenants(engine: AsyncEngine) -> None:
    a, b = await two_workspaces(engine)
    async with engine.begin() as conn:
        mine = await ProjectRepository(conn, a).create(name="Mine", slug="coding")
        theirs = await ProjectRepository(conn, b).create(name="Theirs", slug="coding")
    async with engine.connect() as conn:
        repo_a, repo_b = ProjectRepository(conn, a), ProjectRepository(conn, b)
        assert await repo_a.get(mine.id) == mine
        assert await repo_b.get(mine.id) is None  # another tenant's id is simply not found
        assert await repo_a.get(theirs.id) is None
        assert await repo_a.get_by_slug("coding") == mine  # same slug, different tenant
        assert await repo_b.get_by_slug("coding") == theirs
        assert [p.id for p in await repo_a.list()] == [mine.id]


async def test_duplicate_project_slug_in_a_workspace_is_reported_cleanly(
    engine: AsyncEngine,
) -> None:
    a, _ = await two_workspaces(engine)
    async with engine.begin() as conn:
        repo = ProjectRepository(conn, a)
        await repo.create(name="One", slug="same")
        with pytest.raises(AlreadyExistsError):
            await repo.create(name="Two", slug="same")
        # the transaction is still usable: no IntegrityError aborted it
        assert len(await repo.list()) == 1


async def test_agent_ensure_is_idempotent_and_race_safe(engine: AsyncEngine) -> None:
    a, _ = await two_workspaces(engine)
    async with engine.begin() as conn:
        project = await ProjectRepository(conn, a).create(name="P", slug="p")

    async def ensure() -> uuid.UUID:
        async with engine.begin() as conn:
            agent = await AgentRepository(conn, a).ensure(project_id=project.id, slug="coder")
            return agent.id

    ids = await asyncio.gather(*[ensure() for _ in range(25)])
    assert len(set(ids)) == 1
    async with engine.connect() as conn:
        total = (await conn.execute(select(func.count()).select_from(t.agents))).scalar_one()
    assert total == 1


async def test_agent_versions_are_deduplicated_per_agent(engine: AsyncEngine) -> None:
    a, _ = await two_workspaces(engine)
    async with engine.begin() as conn:
        project = await ProjectRepository(conn, a).create(name="P", slug="p")
        agents = AgentRepository(conn, a)
        coder = await agents.ensure(project_id=project.id, slug="coder")
        reviewer = await agents.ensure(project_id=project.id, slug="reviewer")
        v1 = await agents.ensure_version(agent_id=coder.id, fingerprint="sha256:aa")
        assert v1 == await agents.ensure_version(agent_id=coder.id, fingerprint="sha256:aa")
        assert v1 != await agents.ensure_version(agent_id=coder.id, fingerprint="sha256:bb")
        assert v1 != await agents.ensure_version(agent_id=reviewer.id, fingerprint="sha256:aa")


async def test_a_tenant_cannot_create_an_agent_under_another_tenants_project(
    engine: AsyncEngine,
) -> None:
    from sqlalchemy.exc import IntegrityError

    a, b = await two_workspaces(engine)
    async with engine.begin() as conn:
        project_a = await ProjectRepository(conn, a).create(name="P", slug="p")
    with pytest.raises(IntegrityError):  # the composite foreign key backs up the application
        async with engine.begin() as conn:
            await AgentRepository(conn, b).ensure(project_id=project_a.id, slug="intruder")
