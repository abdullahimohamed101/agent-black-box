"""`ArtifactRepository.redaction_kinds` (review P2-4): the findings job reads the redaction records
of a run's artifacts through the artifacts module, not through its table."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

import abb_api.findings as findings_package
from abb_api.artifacts.repository import ArtifactRecord, ArtifactRepository
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant, make_tenant

KINDS = frozenset({"aws_access_key", "api_key", "jwt"})


async def two(engine: AsyncEngine) -> tuple[Tenant, Tenant]:
    a = await make_tenant(engine, "acme", projects=("alpha", "beta"))
    b = await make_tenant(engine, "globex", projects=("alpha",))
    return a, b


async def put(
    engine: AsyncEngine,
    tenant: TenantContext,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    redaction: dict[str, object] | None,
) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    async with engine.begin() as conn:
        assert await ArtifactRepository(conn, tenant).insert(
            ArtifactRecord(
                id=artifact_id,
                run_id=run_id,
                project_id=project_id,
                kind="terminal_output",
                name=None,
                media_type="text/plain",
                size_bytes=1,
                sha256=b"\x00" * 32,
                storage_key=f"k/{artifact_id}",
                created_at=datetime.now(UTC),
                redaction=redaction,
            )
        )
    return artifact_id


async def test_only_the_runs_artifacts_with_a_requested_kind_are_returned(
    engine: AsyncEngine,
) -> None:
    a, _ = await two(engine)
    alpha = a.project_uuids["alpha"]
    run, other_run = uuid.uuid4(), uuid.uuid4()
    hit = await put(
        engine, a.context, run, alpha, {"server_count": 2, "server_kinds": ["jwt", "cookie"]}
    )
    two_kinds = await put(
        engine, a.context, run, alpha, {"server_kinds": ["aws_access_key", "api_key", "cookie"]}
    )
    await put(engine, a.context, run, alpha, {"server_kinds": ["cookie"]})  # no requested kind
    await put(engine, a.context, run, alpha, None)  # nothing recorded
    await put(engine, a.context, other_run, alpha, {"server_kinds": ["jwt"]})  # another run
    async with engine.connect() as conn:
        found = await ArtifactRepository(conn, a.context).redaction_kinds(
            run, alpha, kinds=KINDS, limit=10
        )
    assert found == {hit: ("jwt",), two_kinds: ("api_key", "aws_access_key")}


async def test_the_limit_bounds_the_artifacts_considered(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    alpha = a.project_uuids["alpha"]
    run = uuid.uuid4()
    for _ in range(5):
        await put(engine, a.context, run, alpha, {"server_kinds": ["jwt"]})
    async with engine.connect() as conn:
        found = await ArtifactRepository(conn, a.context).redaction_kinds(
            run, alpha, kinds=KINDS, limit=3
        )
    assert len(found) == 3


async def test_another_workspace_or_project_sees_nothing_of_the_run(engine: AsyncEngine) -> None:
    a, b = await two(engine)
    alpha, beta = a.project_uuids["alpha"], a.project_uuids["beta"]
    run = uuid.uuid4()
    await put(engine, a.context, run, alpha, {"server_kinds": ["jwt"]})
    async with engine.connect() as conn:
        mine = ArtifactRepository(conn, a.context)
        theirs = ArtifactRepository(conn, b.context)
        assert await mine.redaction_kinds(run, alpha, kinds=KINDS, limit=10)
        assert await mine.redaction_kinds(run, beta, kinds=KINDS, limit=10) == {}
        assert await theirs.redaction_kinds(run, alpha, kinds=KINDS, limit=10) == {}
        assert (
            await theirs.redaction_kinds(run, b.project_uuids["alpha"], kinds=KINDS, limit=10) == {}
        )


def test_the_findings_module_does_not_read_the_artifacts_table() -> None:
    """Modules talk through repositories, not each other's tables (AGENTS.md, INV-7)."""
    root = Path(findings_package.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "t.artifacts" in path.read_text() or "tables.artifacts" in path.read_text()
    ]
    assert offenders == []
