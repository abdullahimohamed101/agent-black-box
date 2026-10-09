"""Members and invitations of a workspace: the rules, and the request/response shapes (D12).

The routes live in `members_router.py`; everything that inspects a role lives here and in
`repository.py`, which the no-scattered-checks test allows to do so.
"""

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Annotated, Literal

from abb_event_schema.ids import IdKind
from fastapi import Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditEntry, record_audit
from abb_api.auth.repository import UserRepository
from abb_api.auth.service import SessionContext
from abb_api.authz.audit import audit_allowed
from abb_api.authz.dependencies import workspace_not_found
from abb_api.authz.members import authorize_role_change
from abb_api.authz.principal import Principal
from abb_api.core.domain import AlreadyExistsError
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.tenancy import TenantContext
from abb_api.workspaces.repository import (
    Invitation,
    InvitationRepository,
    Member,
    MembershipRepository,
    find_invitation_by_hash,
    workspace_summary,
)

logger = logging.getLogger(__name__)

MAX_MEMBERS = 500
MAX_OPEN_INVITATIONS = 200
INVITATION_TTL = timedelta(days=7)

RoleName = Literal["OWNER", "ADMIN", "DEVELOPER", "VIEWER", "SECURITY", "BILLING"]
Email = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=254,
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[^@\s\x00-\x1f\x7f-\x9f]+@[^@\s\x00-\x1f\x7f-\x9f]+\.[^@\s\x00-\x1f\x7f-\x9f]+$",
    ),
]


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str | None
    role: RoleName
    joined_at: str


class MemberList(BaseModel):
    items: list[MemberOut]


class ChangeRole(BaseModel):
    role: RoleName


class InviteIn(BaseModel):
    email: Email
    role: RoleName


class InvitationOut(BaseModel):
    """An invitation as listed. It never carries the token (it exists only in the link, once)."""

    id: str
    email: str
    role: RoleName
    invited_by: str | None
    created_at: str
    expires_at: str


class InvitationList(BaseModel):
    items: list[InvitationOut]


class InvitationCreated(BaseModel):
    invitation: InvitationOut
    link: str = Field(
        description="Shown once: give it to the invitee. The token is in the fragment."
    )


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class AcceptedWorkspace(BaseModel):
    id: str
    slug: str
    name: str


class Accepted(BaseModel):
    workspace: AcceptedWorkspace
    role: RoleName


def member_not_found() -> AppError:
    return AppError(
        "MEMBER_NOT_FOUND", "Member not found.", category=ErrorCategory.NOT_FOUND, status_code=404
    )


def invitation_not_found() -> AppError:
    return AppError(
        "INVITATION_NOT_FOUND",
        "Invitation not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def _conflict(code: str, message: str) -> AppError:
    return AppError(code, message, category=ErrorCategory.CONFLICT, status_code=409)


def limit_reached(what: str, limit: int) -> AppError:
    return _conflict("LIMIT_REACHED", f"A workspace holds at most {limit} {what}.")


def _member_out(member: Member) -> MemberOut:
    return MemberOut(
        user_id=public_id(IdKind.USER, member.user_id),
        email=member.email,
        name=member.name,
        role=member.role,  # type: ignore[arg-type]
        joined_at=member.joined_at.isoformat(),
    )


def _invitation_out(invitation: Invitation) -> InvitationOut:
    return InvitationOut(
        id=public_id(IdKind.INVITATION, invitation.id),
        email=invitation.email,
        role=invitation.role,  # type: ignore[arg-type]
        invited_by=public_id(IdKind.USER, invitation.invited_by) if invitation.invited_by else None,
        created_at=invitation.created_at.isoformat(),
        expires_at=invitation.expires_at.isoformat(),
    )


def hash_invitation_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


# ------------------------------------------------------------------ members


async def list_members(request: Request, principal: Principal) -> MemberList:
    async with _engine(request).connect() as conn:
        members = await MembershipRepository(conn, principal.tenant).list_members(MAX_MEMBERS)
    return MemberList(items=[_member_out(m) for m in members])


async def change_role(
    request: Request, principal: Principal, user_id: str, body: ChangeRole
) -> MemberOut:
    target_id = parse_public_id(IdKind.USER, user_id)
    if target_id is None:
        raise member_not_found()
    async with _engine(request).begin() as conn:
        members = MembershipRepository(conn, principal.tenant)
        owners = await members.lock_owners()  # first, in a fixed order: no lock-order deadlocks
        target = await members.get(target_id, for_update=True)
        if target is None:
            raise member_not_found()
        await authorize_role_change(request, principal, current=target.role, new=body.role)
        if target.role == body.role:
            return _member_out(target)  # nothing changes, nothing is audited
        if target.role == "OWNER" and len(owners) <= 1:
            raise _conflict("LAST_OWNER", "A workspace needs at least one owner.")
        await members.set_role(target_id, body.role, request.app.state.clock())
        updated = await members.get(target_id)
    assert updated is not None
    await audit_allowed(
        request, principal, "member.update", resource_kind="member",
        resource_id=user_id, **{"from": target.role, "to": updated.role},
    )  # fmt: skip
    return _member_out(updated)


async def remove_member(request: Request, principal: Principal, user_id: str) -> None:
    target_id = parse_public_id(IdKind.USER, user_id)
    if target_id is None:
        raise member_not_found()
    async with _engine(request).begin() as conn:
        members = MembershipRepository(conn, principal.tenant)
        owners = await members.lock_owners()
        target = await members.get(target_id, for_update=True)
        if target is None:
            raise member_not_found()
        await authorize_role_change(request, principal, current=target.role, new=None)
        if target.role == "OWNER" and len(owners) <= 1:
            raise _conflict("LAST_OWNER", "A workspace needs at least one owner.")
        await members.remove(target_id)
    await audit_allowed(
        request, principal, "member.remove", resource_kind="member", resource_id=user_id,
        role=target.role,
    )  # fmt: skip


# ------------------------------------------------------------------ invitations


async def list_invitations(request: Request, principal: Principal) -> InvitationList:
    now = request.app.state.clock()
    async with _engine(request).connect() as conn:
        found = await InvitationRepository(conn, principal.tenant).list_open(
            now, MAX_OPEN_INVITATIONS
        )
    return InvitationList(items=[_invitation_out(i) for i in found])


def _link(request: Request, token: str) -> str:
    origin = request.app.state.settings.web_origin_normalised
    if origin is None:  # unreachable for cookie callers (CSRF needs it); defensive
        raise AppError(
            "AUTH_NOT_CONFIGURED",
            "Invitations need ABB_WEB_ORIGIN.",
            category=ErrorCategory.DEPENDENCY,
            status_code=503,
        )
    return f"{origin}/invite#{token}"  # the fragment never reaches a server or proxy log


async def create_invitation(
    request: Request, principal: Principal, body: InviteIn
) -> InvitationCreated:
    await authorize_role_change(request, principal, current=None, new=body.role)
    now = request.app.state.clock()
    token = secrets.token_urlsafe(32)
    invitation_id = new_uuid(IdKind.INVITATION)
    async with _engine(request).begin() as conn:
        members = MembershipRepository(conn, principal.tenant)
        invitations = InvitationRepository(conn, principal.tenant)
        await members.lock_workspace()
        await invitations.retire_expired(now)
        existing = await UserRepository(conn).find_by_email(body.email)
        if existing is not None and await members.role_of(existing.id) is not None:
            raise _conflict("ALREADY_MEMBER", "That person is already a member.")
        if await invitations.has_open_for(body.email, now):
            raise _conflict("ALREADY_INVITED", "That email already has an open invitation.")
        if await invitations.count_open(now) >= MAX_OPEN_INVITATIONS:
            raise limit_reached("open invitations", MAX_OPEN_INVITATIONS)
        try:
            created = await invitations.create(
                invitation_id,
                email=body.email,
                role=body.role,
                token_hash=hash_invitation_token(token),
                invited_by=principal.user_id,
                expires_at=now + INVITATION_TTL,
            )
        except AlreadyExistsError:
            raise _conflict(
                "ALREADY_INVITED", "That email already has an open invitation."
            ) from None
    out = _invitation_out(created)
    await audit_allowed(
        request, principal, "invitation.create", resource_kind="invitation", resource_id=out.id,
        role=body.role,
    )  # fmt: skip
    return InvitationCreated(invitation=out, link=_link(request, token))


async def revoke_invitation(request: Request, principal: Principal, invitation_id: str) -> None:
    found_id = parse_public_id(IdKind.INVITATION, invitation_id)
    if found_id is None:
        raise invitation_not_found()
    now = request.app.state.clock()
    async with _engine(request).begin() as conn:
        if not await InvitationRepository(conn, principal.tenant).revoke(found_id, now):
            raise invitation_not_found()
    await audit_allowed(
        request, principal, "invitation.revoke", resource_kind="invitation",
        resource_id=invitation_id,
    )  # fmt: skip


async def accept_invitation(request: Request, context: SessionContext, body: AcceptIn) -> Accepted:
    """A signed-in person joins the workspace that invited their verified email."""
    now = request.app.state.clock()
    async with _engine(request).begin() as conn:
        invitation = await find_invitation_by_hash(conn, hash_invitation_token(body.token))
        if invitation is None:
            raise invitation_not_found()
        chosen = request.headers.get("x-abb-workspace")
        if chosen is not None and chosen != public_id(IdKind.WORKSPACE, invitation.workspace_id):
            raise workspace_not_found()  # a header naming another workspace is never ignored
        tenant = TenantContext(invitation.workspace_id)
        members = MembershipRepository(conn, tenant)
        invitations = InvitationRepository(conn, tenant)
        await members.lock_workspace()  # serialises concurrent accepts and the member bound
        current = await invitations.get(invitation.id)
        if current is None or current.revoked_at is not None:
            raise invitation_not_found()
        if current.accepted_at is not None:
            raise _conflict("INVITATION_USED", "That invitation was already accepted.")
        if current.expires_at <= now:
            raise AppError(
                "INVITATION_EXPIRED",
                "That invitation has expired; ask for a new one.",
                category=ErrorCategory.VALIDATION,
                status_code=410,
            )
        user = context.user
        if user.email_verified_at is None or user.email != current.email:
            raise AppError(
                "INVITATION_EMAIL_MISMATCH",
                "This invitation was sent to a different, or an unverified, email address.",
                category=ErrorCategory.AUTHORIZATION,
                status_code=403,
            )
        if await members.role_of(user.id) is not None:
            raise _conflict("ALREADY_MEMBER", "You are already a member of that workspace.")
        if await members.count() >= MAX_MEMBERS:
            raise limit_reached("members", MAX_MEMBERS)
        await members.add(user.id, current.role, invited_by=current.invited_by)
        if not await invitations.mark_accepted(current.id, user.id, now):
            # Revoked (or accepted) between the read above and this write: nothing may be admitted.
            raise invitation_not_found()  # the transaction rolls the membership back
        summary = await workspace_summary(conn, current.workspace_id)
    assert summary is not None
    actor = f"user:{public_id(IdKind.USER, context.user.id)}"
    await record_audit(
        _engine(request),
        invitation.workspace_id,
        AuditEntry(
            actor_kind="user", actor_id=actor, action="invitation.accept",
            resource_kind="invitation", resource_id=public_id(IdKind.INVITATION, current.id),
            details={"role": current.role},
        ),
    )  # fmt: skip
    return Accepted(
        workspace=AcceptedWorkspace(
            id=public_id(IdKind.WORKSPACE, invitation.workspace_id),
            slug=summary[0],
            name=summary[1],
        ),
        role=current.role,  # type: ignore[arg-type]
    )
