"""What an authorization decision leaves behind: allowed admin actions and (bounded) denials (D11).

Every mutating administrative action is recorded after it committed. Denials of people are recorded
too, but through a per-actor token bucket: a flood of denied requests costs a few rows and then
only log lines, so the audit table cannot be filled by a client that is merely refused. A failed
audit write is logged and ignored: the response never depends on it.
"""

import logging
from typing import Any

from fastapi import Request

from abb_api.audit.repository import ActorKind, AuditEntry, record_audit
from abb_api.authz.principal import Principal
from abb_api.authz.service import Owned, PermissionDenied, authorize
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, RateLimiter

logger = logging.getLogger(__name__)

_KIND: dict[str, ActorKind] = {"user": "user", "api_key": "api_key"}


def new_denial_limiter(per_minute: int) -> RateLimiter:
    """Allows a burst of `per_minute` denial rows per actor, refilled evenly over a minute."""
    return InMemoryRateLimiter(
        events_per_second=per_minute / 60,
        burst_events=per_minute,
        bytes_per_second=1.0,
        burst_bytes=1,
        max_keys=10_000,
    )


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", "unknown"))[:150]


async def note_denial(request: Request, principal: Principal, action: str) -> None:
    """Log every denial; append an audit row for people, within their bucket."""
    logger.info(
        "permission denied",
        extra={"actor_id": principal.actor_id, "required_permission": action},
    )
    if principal.kind != "user":
        return  # keys are logged structurally, not audited (D11)
    limiter: RateLimiter = request.app.state.denial_limiter
    if limiter.acquire(principal.actor_id, events=1, bytes_=0) is not None:
        return
    await record_audit(
        request.app.state.engine,
        principal.workspace_id,
        AuditEntry(
            actor_kind="user",
            actor_id=principal.actor_id,
            action=action,
            outcome="denied",
            details={"method": request.method, "route": _route_template(request)},
        ),
    )


async def authorize_audited(
    request: Request, principal: Principal, action: str, resource: Owned | None = None
) -> None:
    """`authorize()` that also records the denial. For checks that need the loaded resource."""
    try:
        authorize(principal, action, resource)
    except PermissionDenied:
        await note_denial(request, principal, action)
        raise


async def audit_allowed(
    request: Request,
    principal: Principal,
    action: str,
    *,
    resource_kind: str | None = None,
    resource_id: str | None = None,
    **details: Any,
) -> None:
    """Record a committed administrative action by `principal`."""
    await record_audit(
        request.app.state.engine,
        principal.workspace_id,
        AuditEntry(
            actor_kind=_KIND[principal.kind],
            actor_id=principal.actor_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
            details=details,
        ),
    )
