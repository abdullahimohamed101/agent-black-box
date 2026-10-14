"""The closed set of shared-limiter scopes (ADR-152).

A scope names one budget. Adding a limit means adding a member here (and nowhere else in the
table), so `rate_limit_counters` can be audited by listing this enum. Tenant scopes always carry
a workspace id (INV-3); the one global scope is a ceiling that protects the database itself.
"""

import uuid
from enum import StrEnum

# Counters of a global scope are stored under this workspace id, which no workspace has.
GLOBAL_WORKSPACE = uuid.UUID(int=0)


class Scope(StrEnum):
    INGEST_EVENTS = "ingest_events"
    INGEST_BYTES = "ingest_bytes"
    ARTIFACT_COUNT = "artifact_count"
    ARTIFACT_BYTES = "artifact_bytes"
    LOGIN_START = "login_start"
    POLICY_WRITE = "policy_write"
    POLICY_VALIDATE = "policy_validate"
    POLICY_DRY_RUN = "policy_dry_run"
    APPROVAL_DECIDE_KEY = "approval_decide_key"
    APPROVAL_DECIDE_WORKSPACE = "approval_decide_workspace"
    ALERT_WRITE = "alert_write"
    ALERT_PREVIEW = "alert_preview"
    ALERT_TEST = "alert_test"

    @property
    def is_global(self) -> bool:
        return self is Scope.LOGIN_START


def workspace_for(scope: Scope, workspace_id: uuid.UUID | None) -> uuid.UUID:
    """The workspace a counter is stored under: needed by tenant scopes, refused by global ones."""
    if scope.is_global:
        if workspace_id is not None:
            raise ValueError(f"scope {scope.value} is global and takes no workspace")
        return GLOBAL_WORKSPACE
    if workspace_id is None:
        raise ValueError(f"scope {scope.value} is tenant-scoped and needs a workspace")
    return workspace_id
