"""Publishing a policy version (ADR-140): validate, number, store and audit in one transaction.

The audit row names the scope, the new version, the document's SHA-256 and its rule count, and
never the text: audit rows are read by more roles than policies are, and a policy can describe
what an organisation guards. A failed validation writes nothing, including no audit row.
"""

import hashlib
import uuid
from collections.abc import Sequence

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.core.domain import DomainError
from abb_api.ids import public_id
from abb_api.policies.parser import Issue, compile_cached, parse_policy
from abb_api.policies.store import PgPolicyStore, StoredPolicy
from abb_api.security.settings import Actor
from abb_api.tenancy import TenantContext

PUBLISH_AUDIT_ACTION = "policy.publish"
MAX_COMMENT_LENGTH = 500


class PolicyInvalidError(DomainError):
    """The document does not validate. `issues` carry line, column, code and a fixed message."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        super().__init__("the policy document is not valid")
        self.issues = tuple(issues)


class VersionConflictError(DomainError):
    """Someone published a newer version than the one the editor started from."""

    def __init__(self, current: int) -> None:
        super().__init__("the policy changed since it was loaded")
        self.current = current


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def publish_policy(
    conn: AsyncConnection,
    tenant: TenantContext,
    project_id: uuid.UUID | None,
    actor: Actor,
    document: str,
    *,
    comment: str | None = None,
    base_version: int | None = None,
) -> StoredPolicy:
    """Validate `document`, store it as the scope's next version and append `policy.publish`,
    on the caller's transaction. `base_version` is the version the editor started from (0: there
    was none); a different current version is `VersionConflictError`, so two editors cannot
    silently overwrite each other."""
    parsed = parse_policy(document)
    if parsed.policy is None:
        raise PolicyInvalidError(parsed.issues)
    store = PgPolicyStore(conn, tenant)
    await store.lock_scope(project_id)
    if base_version is not None:
        current = await store.latest(project_id)
        current_version = current.version if current else 0
        if base_version != current_version:
            raise VersionConflictError(current_version)
    stored = await store.append(
        project_id,
        document_text=document,
        document_sha256=sha256_of(document),
        rule_count=len(parsed.policy.rules),
        created_by=actor.id[:128],
        comment=comment,
    )
    resource = (
        public_id(IdKind.PROJECT, project_id)
        if project_id is not None
        else public_id(IdKind.WORKSPACE, tenant.workspace_id)
    )
    await AuditRepository(conn, tenant).append(
        AuditEntry(
            actor_kind=actor.kind,
            actor_id=actor.id,
            action=PUBLISH_AUDIT_ACTION,
            resource_kind="policy",
            resource_id=resource,
            details={
                "scope": "project" if project_id is not None else "workspace",
                "version": stored.version,
                "sha256": stored.document_sha256,
                "rules": stored.rule_count,
            },
        )
    )
    compile_cached(document)  # warm the cache for the first decision that needs it
    return stored
