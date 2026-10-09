"""Who may touch an OWNER (spec §91): the one rule that depends on the roles involved.

`member.write` lets an ADMIN manage everyone except owners. Any change in which the member's
current role or the role being assigned is OWNER additionally needs `member.write_owner`, so an
ADMIN can neither demote or remove an owner nor make anyone (themselves included) an owner.
"""

from fastapi import Request

from abb_api.authz import actions
from abb_api.authz.audit import authorize_audited
from abb_api.authz.matrix import OWNER
from abb_api.authz.principal import Principal


async def authorize_role_change(
    request: Request, principal: Principal, *, current: str | None, new: str | None
) -> None:
    """`current`: the role now (None when inviting); `new`: the role after (None when removing)."""
    if OWNER in (current, new):
        await authorize_audited(request, principal, actions.MEMBER_WRITE_OWNER)
