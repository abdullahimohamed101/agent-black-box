"""Tenant context: the workspace every tenant-data operation must carry (INV-3)."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    workspace_id: uuid.UUID
