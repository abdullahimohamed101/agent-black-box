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
    # Revoking one's own sessions: keyed by the person (the sessions page; Phase 19a step 5).
    SESSION_WRITE = "session_write"
    POLICY_WRITE = "policy_write"
    POLICY_VALIDATE = "policy_validate"
    POLICY_DRY_RUN = "policy_dry_run"
    APPROVAL_DECIDE_KEY = "approval_decide_key"
    APPROVAL_DECIDE_WORKSPACE = "approval_decide_workspace"
    ALERT_WRITE = "alert_write"
    ALERT_PREVIEW = "alert_preview"
    ALERT_TEST = "alert_test"
    # Retention policy changes and the preview of what a policy would delete (ADR-159).
    RETENTION_WRITE = "retention_write"
    RETENTION_PREVIEW = "retention_preview"
    # Failed API-key authentication (ADR-153). Counted before any tenant is known, so these are
    # global scopes keyed by a pseudonymous client address, a presented key id, or one fixed key.
    AUTH_FAIL_ADDRESS = "auth_fail_address"
    AUTH_FAIL_CREDENTIAL = "auth_fail_credential"
    AUTH_FAIL_GLOBAL = "auth_fail_global"
    # Failed session cookies (ADR-169, KI-176): the same shape, keyed by a pseudonymous client
    # address or one fixed key. No per-credential scope: a session token is 256 random bits, so
    # a per-token budget bounds nothing (a memory cache of recent failures does).
    SESSION_FAIL_ADDRESS = "session_fail_address"
    SESSION_FAIL_GLOBAL = "session_fail_global"
    # At most one "throttled" audit row per workspace per window; tenant-scoped.
    AUTH_THROTTLE_AUDIT = "auth_throttle_audit"

    @property
    def is_global(self) -> bool:
        return self in _GLOBAL_SCOPES


_GLOBAL_SCOPES = frozenset(
    {
        Scope.LOGIN_START,
        Scope.SESSION_WRITE,
        Scope.AUTH_FAIL_ADDRESS,
        Scope.AUTH_FAIL_CREDENTIAL,
        Scope.AUTH_FAIL_GLOBAL,
        Scope.SESSION_FAIL_ADDRESS,
        Scope.SESSION_FAIL_GLOBAL,
    }
)


def workspace_for(scope: Scope, workspace_id: uuid.UUID | None) -> uuid.UUID:
    """The workspace a counter is stored under: needed by tenant scopes, refused by global ones."""
    if scope.is_global:
        if workspace_id is not None:
            raise ValueError(f"scope {scope.value} is global and takes no workspace")
        return GLOBAL_WORKSPACE
    if workspace_id is None:
        raise ValueError(f"scope {scope.value} is tenant-scoped and needs a workspace")
    return workspace_id
