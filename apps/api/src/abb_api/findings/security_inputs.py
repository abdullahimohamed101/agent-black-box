"""What the security detectors read besides a run's events (ADR-131): the project's settings and
the redaction records of the run's artifacts. Configuration and metadata only; artifact bodies are
never read here."""

import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.findings.model import SecurityInputs
from abb_api.ids import public_id
from abb_api.security.scanner import HIGH_CONFIDENCE_KINDS
from abb_api.security.settings import PgSecuritySettingsStore
from abb_api.tenancy import TenantContext

MAX_ARTIFACTS = 200  # artifacts of one run whose redaction record is considered


async def load_security_inputs(
    conn: AsyncConnection, tenant: TenantContext, project_id: uuid.UUID, run_id: uuid.UUID
) -> SecurityInputs:
    settings = await PgSecuritySettingsStore(conn, tenant).get(project_id)
    column = t.artifacts.c["metadata"]["redaction"]["server_kinds"]
    rows = await conn.execute(
        select(t.artifacts.c.id, column)
        .where(
            t.artifacts.c.workspace_id == tenant.workspace_id,
            t.artifacts.c.run_id == run_id,
            t.artifacts.c.project_id == project_id,
            column.has_any(array(sorted(HIGH_CONFIDENCE_KINDS))),
        )
        .order_by(t.artifacts.c.id)
        .limit(MAX_ARTIFACTS)
    )
    artifact_kinds: dict[str, tuple[str, ...]] = {}
    for artifact_id, kinds in rows:
        if isinstance(kinds, list):
            artifact_kinds[public_id(IdKind.ARTIFACT, artifact_id)] = tuple(
                sorted({k for k in kinds if isinstance(k, str) and k in HIGH_CONFIDENCE_KINDS})
            )
    return SecurityInputs(
        muted=frozenset(settings.muted_detectors),
        destination_allowlist=settings.destination_allowlist,
        artifact_kinds=artifact_kinds,
    )
