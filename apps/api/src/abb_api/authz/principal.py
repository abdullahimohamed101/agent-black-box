"""The authenticated actor: an API key now, a signed-in user once sessions exist (ADR-061)."""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class Principal:
    """Who is calling, which workspace they act in, and the actions they hold there.

    Keys and users share this one type so there is a single enforcement path. Nothing outside
    `authz/` and `auth/` should look inside `actions`: ask `authorize()`.
    """

    kind: Literal["api_key", "user"]
    workspace_id: uuid.UUID
    # None: a workspace-wide key or a user (may read across projects; cannot ingest).
    project_id: uuid.UUID | None
    actions: frozenset[str]
    actor_id: str  # "key:<key_id>" or "user:<usr_id>": safe to log, the stream-limit key
    # Actions held only on resources this actor created (resource.created_by == user_id).
    own_actions: frozenset[str] = frozenset()
    user_id: uuid.UUID | None = None
    session_id: str | None = field(default=None, repr=False)

    @property
    def tenant(self) -> TenantContext:
        return TenantContext(self.workspace_id)
