"""The versioned policy store: numbering, resolution, immutability, atomic publish with its audit
row, concurrency and tenant isolation (ADR-140, INV-1, INV-3)."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.audit.repository import AuditRepository
from abb_api.core.domain import NotFoundError
from abb_api.policies.service import (
    PolicyInvalidError,
    VersionConflictError,
    publish_policy,
    sha256_of,
)
from abb_api.policies.store import PgPolicyStore
from abb_api.security.settings import Actor
from tests.conftest import runtime_url
from tests.ingest_helpers import Tenant, make_tenant

ALICE = Actor("user", "user:usr_alice")
SECRET = "SECRET-PATH-sentinel-91c2"


def doc(n: int, *, description: str = "one") -> str:
    return (
        f"version: 1\npolicies:\n  - id: rule-{n}\n    description: {description}\n"
        "    when: {capability: filesystem.read}\n    decision: deny\n"
    )


@pytest.fixture
async def runtime(database_url: str, runtime_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    yield engine
    await engine.dispose()


async def two(engine: AsyncEngine) -> tuple[Tenant, Tenant]:
    return (
        await make_tenant(engine, "acme", projects=("alpha", "beta")),
        await make_tenant(engine, "globex", projects=("p",)),
    )


async def publish(
    engine: AsyncEngine, tenant: Tenant, project: uuid.UUID | None, n: int = 1, **kw: object
) -> int:
    async with engine.begin() as conn:
        stored = await publish_policy(conn, tenant.context, project, ALICE, doc(n), **kw)  # type: ignore[arg-type]
    return stored.version


async def test_versions_are_numbered_per_scope_and_never_reused(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    alpha, beta = a.project_uuids["alpha"], a.project_uuids["beta"]
    assert [await publish(runtime, a, None, n) for n in (1, 2, 3)] == [1, 2, 3]
    assert [await publish(runtime, a, alpha, n) for n in (1, 2)] == [1, 2]
    assert await publish(runtime, a, beta) == 1
    assert await publish(runtime, a, None) == 4
    async with runtime.connect() as conn:
        store = PgPolicyStore(conn, a.context)
        history = await store.history(None, limit=10)
        assert [v.version for v in history] == [4, 3, 2, 1]
        assert [v.version for v in await store.history(None, limit=2, before_version=3)] == [2, 1]
        assert [v.version for v in await store.history(alpha, limit=10)] == [2, 1]
        got = await store.get(None, 2)
        assert got is not None and got.document_text == doc(2)
        assert got.document_sha256 == sha256_of(doc(2)) and got.rule_count == 1
        assert got.created_by == "user:usr_alice" and got.project_id is None
        assert await store.get(None, 9) is None


async def test_the_effective_policy_is_the_project_then_the_workspace_then_nothing(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    alpha, beta = a.project_uuids["alpha"], a.project_uuids["beta"]
    async with runtime.connect() as conn:
        assert await PgPolicyStore(conn, a.context).effective(alpha) == ("default", None)
        assert await PgPolicyStore(conn, a.context).effective(None) == ("default", None)
    await publish(runtime, a, None, 1)
    async with runtime.connect() as conn:
        source, stored = await PgPolicyStore(conn, a.context).effective(alpha)
        assert source == "workspace" and stored is not None and stored.version == 1
    await publish(runtime, a, alpha, 2)
    await publish(runtime, a, None, 3)
    async with runtime.connect() as conn:
        store = PgPolicyStore(conn, a.context)
        source, stored = await store.effective(alpha)
        assert source == "project" and stored is not None and stored.document_text == doc(2)
        source, stored = await store.effective(beta)  # beta has none: inherits the base (v2)
        assert source == "workspace" and stored is not None and stored.version == 2
        source, stored = await store.effective(None)
        assert source == "workspace" and stored is not None and stored.version == 2


async def test_published_versions_cannot_be_changed_or_removed_by_the_runtime_role(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    await publish(runtime, a, None)
    for statement in (
        "UPDATE policy_versions SET document_text = 'x'",
        "UPDATE policy_versions SET version = 9",
        "DELETE FROM policy_versions",
        "TRUNCATE policy_versions",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with runtime.begin() as conn:
                await conn.execute(text(statement))
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM policy_versions"))).scalar_one() == 1


async def test_publish_and_its_audit_row_commit_or_roll_back_together(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    comment = f"note {SECRET}"
    version = await publish(runtime, a, None, 1, comment=comment)
    async with runtime.connect() as conn:
        rows = [r for r in await AuditRepository(conn, a.context).recent(10)]
    published = [r for r in rows if r.action == "policy.publish"]
    assert len(published) == 1
    row = published[0]
    assert (row.actor_kind, row.actor_id, row.outcome) == ("user", "user:usr_alice", "allowed")
    assert row.resource_kind == "policy" and row.resource_id == a.workspace_id
    assert row.details == {
        "scope": "workspace",
        "version": version,
        "sha256": sha256_of(doc(1)),
        "rules": 1,
    }
    assert SECRET not in str(row.details) and "rule-1" not in str(row.details)

    # A failure after the insert (the caller's transaction aborts) leaves neither row.
    with pytest.raises(RuntimeError):
        async with runtime.begin() as conn:
            await publish_policy(conn, a.context, None, ALICE, doc(2))
            raise RuntimeError("boom")
    async with runtime.connect() as conn:
        assert [v.version for v in await PgPolicyStore(conn, a.context).history(None, limit=9)] == [
            1
        ]
        assert len([r for r in await AuditRepository(conn, a.context).recent(10)
                    if r.action == "policy.publish"]) == 1  # fmt: skip


async def test_a_project_scope_audit_row_names_the_project(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    await publish(runtime, a, a.project_uuids["alpha"], 1)
    async with runtime.connect() as conn:
        row = next(
            r
            for r in await AuditRepository(conn, a.context).recent(10)
            if r.action == "policy.publish"
        )
    assert row.resource_id == a.projects["alpha"] and row.details["scope"] == "project"


async def test_an_invalid_document_stores_nothing_and_audits_nothing(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    with pytest.raises(PolicyInvalidError) as caught:
        async with runtime.begin() as conn:
            await publish_policy(conn, a.context, None, ALICE, "version: 2\n")
    assert [i.code for i in caught.value.issues] == ["invalid_version"]
    async with runtime.connect() as conn:
        assert await PgPolicyStore(conn, a.context).history(None, limit=5) == []
        assert not [
            r
            for r in await AuditRepository(conn, a.context).recent(10)
            if r.action == "policy.publish"
        ]


async def test_concurrent_publishers_get_distinct_consecutive_versions(
    engine: AsyncEngine, runtime: AsyncEngine
) -> None:
    a, _ = await two(engine)
    results = await asyncio.gather(*(publish(runtime, a, None, n) for n in range(10)))
    assert sorted(results) == list(range(1, 11))
    async with runtime.connect() as conn:
        versions = await PgPolicyStore(conn, a.context).history(None, limit=20)
        audits = [
            r
            for r in await AuditRepository(conn, a.context).recent(50)
            if r.action == "policy.publish"
        ]
    assert sorted(v.version for v in versions) == list(range(1, 11))
    assert sorted(r.details["version"] for r in audits) == list(range(1, 11))


async def test_base_version_stops_a_lost_update(engine: AsyncEngine, runtime: AsyncEngine) -> None:
    a, _ = await two(engine)
    assert await publish(runtime, a, None, 1, base_version=0) == 1
    assert await publish(runtime, a, None, 2, base_version=1) == 2
    with pytest.raises(VersionConflictError) as caught:
        await publish(runtime, a, None, 3, base_version=1)
    assert caught.value.current == 2
    async with runtime.connect() as conn:
        assert len(await PgPolicyStore(conn, a.context).history(None, limit=9)) == 2


async def test_tenants_and_projects_are_isolated(engine: AsyncEngine, runtime: AsyncEngine) -> None:
    a, b = await two(engine)
    alpha = a.project_uuids["alpha"]
    await publish(runtime, a, None, 1)
    await publish(runtime, a, alpha, 2)
    await publish(runtime, b, None, 3)
    async with runtime.connect() as conn:
        theirs = PgPolicyStore(conn, b.context)
        assert [v.created_by for v in await theirs.history(None, limit=9)] == ["user:usr_alice"]
        assert (await theirs.get(None, 1)).document_text == doc(3)  # type: ignore[union-attr]
        # the other workspace cannot name acme's project, in any operation
        with pytest.raises(NotFoundError):
            await theirs.latest(alpha)
        with pytest.raises(NotFoundError):
            await theirs.history(alpha, limit=5)
        with pytest.raises(NotFoundError):
            await theirs.effective(alpha)
        with pytest.raises(NotFoundError):
            await theirs.lock_scope(alpha)
    with pytest.raises(NotFoundError):
        async with runtime.begin() as conn:
            await publish_policy(conn, b.context, alpha, ALICE, doc(9))
    async with runtime.connect() as conn:
        assert await PgPolicyStore(conn, a.context).history(alpha, limit=9) != []
        assert len(await PgPolicyStore(conn, a.context).history(None, limit=9)) == 1


async def test_the_database_itself_refuses_a_foreign_project_and_bad_rows(
    engine: AsyncEngine,
) -> None:
    a, b = await two(engine)
    base = {
        "w": a.context.workspace_id,
        "i": uuid.uuid4(),
        "p": b.project_uuids["p"],
        "t": doc(1),
        "h": sha256_of(doc(1)),
    }
    insert = (
        "INSERT INTO policy_versions (workspace_id, id, project_id, version, document_text, "
        "document_sha256, rule_count, created_by) VALUES (:w, :i, :p, 1, :t, :h, 1, 'x')"
    )
    with pytest.raises(IntegrityError):  # another workspace's project (composite foreign key)
        async with engine.begin() as conn:
            await conn.execute(text(insert), base)
    for bad in (
        {"h": "NOT-HEX", "p": None},
        {"t": "x" * 70000, "p": None},
    ):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text(insert), {**base, **bad})
    with pytest.raises(IntegrityError):  # a duplicate base version is a unique violation
        async with engine.begin() as conn:
            for i in (uuid.uuid4(), uuid.uuid4()):
                await conn.execute(text(insert), {**base, "i": i, "p": None})
