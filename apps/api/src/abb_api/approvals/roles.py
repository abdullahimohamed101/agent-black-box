"""Which signed-in role may decide an approval (ADR-141, D4).

A rule names the role that must approve (`approver_role`). The relation is a ladder, widest
authority first: OWNER, ADMIN, SECURITY, DEVELOPER. A named role is satisfied by itself and by any
role above it; the roles below it are not. An unset `approver_role` means "an owner or an admin".
VIEWER and BILLING never satisfy anything (they cannot read payloads, so they could not see what
they approve), and neither does a role that is not in the ladder.
"""

from typing import Final

OWNER: Final = "OWNER"
ADMIN: Final = "ADMIN"
SECURITY: Final = "SECURITY"
DEVELOPER: Final = "DEVELOPER"

# approver_role -> the roles that may decide. The literal copy in tests/approvals/test_roles.py is
# compared both ways, so changing a row is a deliberate two-place edit.
SATISFIED_BY: Final[dict[str | None, frozenset[str]]] = {
    None: frozenset({OWNER, ADMIN}),
    OWNER: frozenset({OWNER}),
    ADMIN: frozenset({OWNER, ADMIN}),
    SECURITY: frozenset({OWNER, ADMIN, SECURITY}),
    DEVELOPER: frozenset({OWNER, ADMIN, SECURITY, DEVELOPER}),
}


def may_decide(approver_role: str | None, user_role: str | None) -> bool:
    """Whether a member with `user_role` satisfies a rule's `approver_role`."""
    if user_role is None:
        return False
    return user_role in SATISFIED_BY.get(approver_role, frozenset())
