"""Members and invitations (D12). Only people use these: API keys hold none of the actions."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from abb_api.auth.service import SessionContext
from abb_api.authz import actions
from abb_api.authz.dependencies import require, require_user
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.workspaces import members as service
from abb_api.workspaces.members import (
    Accepted,
    AcceptIn,
    ChangeRole,
    InvitationCreated,
    InvitationList,
    InviteIn,
    MemberList,
    MemberOut,
)

router = APIRouter(tags=["members"])

MemberReader = Annotated[Principal, Depends(require(actions.MEMBER_READ))]
MemberWriter = Annotated[Principal, Depends(require(actions.MEMBER_WRITE))]
InviteReader = Annotated[Principal, Depends(require(actions.INVITE_READ))]
InviteWriter = Annotated[Principal, Depends(require(actions.INVITE_WRITE))]
Signed = Annotated[SessionContext, Depends(require_user())]


def _errors(**extra: str) -> dict[int | str, dict[str, Any]]:
    texts = {
        401: "Missing or invalid credential.",
        403: "The caller's role does not allow this.",
        404: "Workspace, member or invitation not found.",
        422: "The request is invalid.",
        503: "A dependency is unavailable; retry with backoff.",
        **{int(k[1:]): v for k, v in extra.items()},
    }
    return {code: {"description": text, "model": ErrorEnvelope} for code, text in texts.items()}


@router.get(
    "/v1/members",
    response_model=MemberList,
    responses=_errors(),
    summary="List the members of the workspace",
    description="Emails and roles are visible to every member (SECURITY.md). At most 500.",
)
async def list_members(request: Request, principal: MemberReader) -> MemberList:
    return await service.list_members(request, principal)


@router.patch(
    "/v1/members/{user_id}",
    response_model=MemberOut,
    responses=_errors(c409="The last owner cannot be demoted."),
    summary="Change a member's role",
    description=(
        "Owners and admins. Any change that involves the OWNER role (current or new) needs an "
        "owner. The last owner cannot be demoted (`LAST_OWNER`)."
    ),
)
async def change_role(
    user_id: str, body: ChangeRole, request: Request, principal: MemberWriter
) -> MemberOut:
    return await service.change_role(request, principal, user_id, body)


@router.delete(
    "/v1/members/{user_id}",
    status_code=204,
    responses=_errors(c409="The last owner cannot be removed."),
    summary="Remove a member",
    description="Same rules as changing a role. A member may be removed by themselves.",
)
async def remove_member(user_id: str, request: Request, principal: MemberWriter) -> Response:
    await service.remove_member(request, principal, user_id)
    return Response(status_code=204)


@router.get(
    "/v1/invitations",
    response_model=InvitationList,
    responses=_errors(),
    summary="List open invitations",
    description="Pending, unexpired invitations. Tokens are never listed.",
)
async def list_invitations(request: Request, principal: InviteReader) -> InvitationList:
    return await service.list_invitations(request, principal)


@router.post(
    "/v1/invitations",
    status_code=201,
    response_model=InvitationCreated,
    responses=_errors(c409="Already a member, already invited, or 200 open invitations."),
    summary="Invite a person by email",
    description=(
        "Returns the invitation link once; the token is in the URL fragment. Inviting as OWNER "
        "needs an owner. Valid for 7 days, for one acceptance, by a verified matching email."
    ),
)
async def create_invitation(
    body: InviteIn, request: Request, principal: InviteWriter
) -> InvitationCreated:
    return await service.create_invitation(request, principal, body)


@router.delete(
    "/v1/invitations/{invitation_id}",
    status_code=204,
    responses=_errors(),
    summary="Revoke an open invitation",
)
async def revoke_invitation(
    invitation_id: str, request: Request, principal: InviteWriter
) -> Response:
    await service.revoke_invitation(request, principal, invitation_id)
    return Response(status_code=204)


@router.post(
    "/v1/invitations/accept",
    response_model=Accepted,
    responses=_errors(
        c409="Already a member, already accepted, or the workspace is full.",
        c410="The invitation has expired.",
    ),
    summary="Accept an invitation",
    description=(
        "For a signed-in person; needs no workspace header (a header naming another workspace "
        "than the invitation's is a 404). The verified email must equal the invited one."
    ),
)
async def accept_invitation(body: AcceptIn, request: Request, context: Signed) -> Accepted:
    return await service.accept_invitation(request, context, body)
