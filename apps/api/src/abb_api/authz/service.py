"""`authorize(actor, action, resource)`: the one place a permission decision is made (spec §91)."""

from typing import Protocol

from abb_api.authz.matrix import scope_for
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory


class Owned(Protocol):
    @property
    def created_by(self) -> object: ...


class PermissionDenied(AppError):
    """403. For keys this is `INSUFFICIENT_SCOPE` (unchanged for existing SDK clients)."""

    def __init__(self, actor: Principal, action: str) -> None:
        if actor.kind == "api_key":
            scope = scope_for(action)
            super().__init__(
                "INSUFFICIENT_SCOPE",
                f"This API key lacks the '{scope}' scope."
                if scope
                else "API keys cannot perform this action.",
                category=ErrorCategory.AUTHORIZATION,
                status_code=403,
                details={
                    **({"required_scope": scope} if scope else {}),
                    "required_permission": action,
                },
            )
        else:
            super().__init__(
                "PERMISSION_DENIED",
                "Your role does not allow this action.",
                category=ErrorCategory.AUTHORIZATION,
                status_code=403,
                details={"required_permission": action},
            )
        self.action = action


def authorize(actor: Principal, action: str, resource: Owned | None = None) -> None:
    """Return if `actor` may perform `action` (on `resource`), else raise `PermissionDenied`."""
    if action in actor.actions:
        return
    if (
        action in actor.own_actions
        and resource is not None
        and actor.user_id is not None
        and resource.created_by == actor.user_id
    ):
        return
    raise PermissionDenied(actor, action)
