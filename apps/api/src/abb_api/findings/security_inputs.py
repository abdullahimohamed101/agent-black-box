"""What the security detectors read besides a run's events (ADR-131): the project's settings and
the redaction records of the run's artifacts. Configuration and metadata only; artifact bodies are
never read here."""

import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.artifacts.repository import ArtifactRepository
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
    recorded = await ArtifactRepository(conn, tenant).redaction_kinds(
        run_id, project_id, kinds=HIGH_CONFIDENCE_KINDS, limit=MAX_ARTIFACTS
    )
    artifact_kinds = {public_id(IdKind.ARTIFACT, id_): kinds for id_, kinds in recorded.items()}
    return SecurityInputs(
        muted=frozenset(settings.muted_detectors),
        destination_allowlist=settings.destination_allowlist,
        artifact_kinds=artifact_kinds,
    )
