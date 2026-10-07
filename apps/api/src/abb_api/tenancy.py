"""Tenant context: the workspace every tenant-data operation must carry (INV-3)."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    workspace_id: uuid.UUID


@dataclass(frozen=True)
class Principal:
    """An authenticated API key, resolved to its tenant and permissions."""

    workspace_id: uuid.UUID
    # None: a workspace-wide key (may read across projects; cannot ingest).
    project_id: uuid.UUID | None
    scopes: frozenset[str]
    key_id: str  # public identifier, safe to log

    @property
    def tenant(self) -> TenantContext:
        return TenantContext(self.workspace_id)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
