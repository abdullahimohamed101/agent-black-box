"""Members and invitations: roles, the last-owner rule, one-time links, bounds, audit (D12)."""

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text

from abb_api.ids import public_id
from tests.api_fixtures import Api, person
from tests.auth_helpers import add_member
from tests.test_audit import rows
from tests.test_runs_api import error

NO_WORKSPACE = "x-abb-workspace"


def token_of(link: str) -> str:
    assert link.startswith("http://localhost:3000/invite#")
    return link.split("#", 1)[1]


async def invite(
    api: Api, headers: dict[str, str], email: str, role: str = "VIEWER"
) -> httpx.Response:
    return await api.client.post(
        "/v1/invitations", json={"email": email, "role": role}, headers=headers
    )


async def accept(api: Api, headers: dict[str, str], token: str) -> httpx.Response:
    return await api.client.post("/v1/invitations/accept", json={"token": token}, headers=headers)


async def role_in_acme(api: Api, email: str) -> str | None:
    async with api.engine.connect() as conn:
        found = await conn.execute(
            text(
                "SELECT m.role FROM workspace_members m JOIN users u ON u.id = m.user_id "
                "WHERE u.email = :e AND m.workspace_id = :w"
            ),
            {"e": email, "w": api.tenant.context.workspace_id},
        )
        row = found.first()
    return row.role if row else None


async def members_of(api: Api, headers: dict[str, str]) -> dict[str, dict[str, str]]:
    listed = (await api.client.get("/v1/members", headers=headers)).json()["items"]
    return {m["email"]: m for m in listed}


# ---------------------------------------------------------------- listing


async def test_every_role_lists_members_and_keys_cannot(web: Api) -> None:
    viewer = await person(web, "v@acme.test", "VIEWER")
    await person(web, "o@acme.test", "OWNER")
    listed = await web.client.get("/v1/members", headers=viewer)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert {(i["email"], i["role"]) for i in items} == {
        ("v@acme.test", "VIEWER"),
        ("o@acme.test", "OWNER"),
    }
    assert all(i["user_id"].startswith("usr_") for i in items)
    denied = error(await web.get("/v1/members", token="wide_reader"), 403, "INSUFFICIENT_SCOPE")
    assert "required_scope" not in denied["details"]


# ---------------------------------------------------------------- invitations


async def test_invite_list_accept_and_the_link_is_single_use(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    admin = await person(web, "a@acme.test", "ADMIN")
    created = await invite(web, admin, "  New.Person@Acme.TEST ", "DEVELOPER")
    assert created.status_code == 201
    body = created.json()
    token = token_of(body["link"])
    assert body["invitation"]["email"] == "new.person@acme.test"  # stored lower-cased
    assert "token" not in body["invitation"] and token not in str(body["invitation"])

    listed = await web.client.get("/v1/invitations", headers=admin)
    assert [i["email"] for i in listed.json()["items"]] == ["new.person@acme.test"]
    assert token not in listed.text

    invitee = await person(web, "new.person@acme.test", None, verified=True)
    no_workspace = {k: v for k, v in invitee.items() if k != NO_WORKSPACE}
    joined = await accept(web, no_workspace, token)
    assert joined.status_code == 200, joined.text
    assert joined.json()["role"] == "DEVELOPER" and joined.json()["workspace"]["slug"] == "acme"
    assert await role_in_acme(web, "new.person@acme.test") == "DEVELOPER"
    assert (await web.client.get("/v1/runs", headers=invitee)).status_code == 200  # now a member
    assert (await web.client.get("/v1/invitations", headers=admin)).json()["items"] == []

    error(await accept(web, invitee, token), 409, "INVITATION_USED")
    # Neither the token nor any email reached a log line.
    assert token not in caplog.text and "new.person@acme.test" not in caplog.text
    found = await rows(web)
    assert [r.action for r in found] == ["invitation.accept", "invitation.create"]
    for row in found:
        assert token not in str(row.details) and "@" not in str(row.details)


async def test_only_the_invited_verified_email_can_accept(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    token = token_of((await invite(web, admin, "right@acme.test")).json()["link"])
    wrong = await person(web, "wrong@acme.test", None, verified=True)
    error(await accept(web, wrong, token), 403, "INVITATION_EMAIL_MISMATCH")
    unverified = await person(web, "right@acme.test", None, verified=False)
    error(await accept(web, unverified, token), 403, "INVITATION_EMAIL_MISMATCH")
    assert await role_in_acme(web, "wrong@acme.test") is None
    assert await role_in_acme(web, "right@acme.test") is None


async def test_accepting_needs_a_session_the_right_origin_and_a_known_token(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    token = token_of((await invite(web, admin, "right@acme.test")).json()["link"])
    invitee = await person(web, "right@acme.test", None, verified=True)
    error(await accept(web, {}, token), 401, "SESSION_INVALID")
    error(
        await accept(web, {**invitee, "origin": "https://evil.test"}, token), 403, "CSRF_REJECTED"
    )
    error(await accept(web, invitee, "x" * 43), 404, "INVITATION_NOT_FOUND")
    bad = await web.client.post("/v1/invitations/accept", json={"token": "short"}, headers=invitee)
    assert bad.status_code == 422
    # A header naming a different workspace than the invitation's is refused, not ignored.
    elsewhere = {**invitee, NO_WORKSPACE: web.other.workspace_id}
    error(await accept(web, elsewhere, token), 404, "WORKSPACE_NOT_FOUND")
    # An API key is not a person.
    keyed = await web.client.post(
        "/v1/invitations/accept", json={"token": token}, headers=web.headers("reader")
    )
    assert keyed.status_code == 401
    assert (await accept(web, invitee, token)).status_code == 200


async def test_expired_revoked_and_reused_links(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    expired = token_of((await invite(web, admin, "late@acme.test")).json()["link"])
    revoked_response = await invite(web, admin, "gone@acme.test")
    revoked = token_of(revoked_response.json()["link"])
    revoked_id = revoked_response.json()["invitation"]["id"]
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE invitations SET expires_at = :t WHERE email = 'late@acme.test'"),
            {"t": web.clock() - timedelta(seconds=1)},
        )
    deleted = await web.client.delete(f"/v1/invitations/{revoked_id}", headers=admin)
    assert deleted.status_code == 204
    late = await person(web, "late@acme.test", None, verified=True)
    gone = await person(web, "gone@acme.test", None, verified=True)
    error(await accept(web, late, expired), 410, "INVITATION_EXPIRED")
    error(await accept(web, gone, revoked), 404, "INVITATION_NOT_FOUND")
    # The lapsed invitation no longer blocks a new one for the same email.
    fresh = await invite(web, admin, "late@acme.test")
    assert fresh.status_code == 201
    assert (await accept(web, late, token_of(fresh.json()["link"]))).status_code == 200
    # A revoked invitation cannot be revoked twice.
    again = await web.client.delete(f"/v1/invitations/{revoked_id}", headers=admin)
    error(again, 404, "INVITATION_NOT_FOUND")


async def test_an_invitation_revoked_during_acceptance_admits_no_one(
    web: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F4: the revoke commits between the acceptor's read and its update."""
    from abb_api.workspaces.repository import InvitationRepository

    admin = await person(web, "a@acme.test", "ADMIN")
    sent = await invite(web, admin, "racer@acme.test")
    token = token_of(sent.json()["link"])
    invitee = await person(web, "racer@acme.test", None, verified=True)
    original = InvitationRepository.mark_accepted

    async def revoke_first(self: InvitationRepository, *args: object, **kwargs: object) -> bool:
        async with web.engine.begin() as other:  # a second connection, committed before we write
            await other.execute(
                text("UPDATE invitations SET revoked_at = now() WHERE email = 'racer@acme.test'")
            )
        return await original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(InvitationRepository, "mark_accepted", revoke_first)
    error(await accept(web, invitee, token), 404, "INVITATION_NOT_FOUND")
    assert await role_in_acme(web, "racer@acme.test") is None


async def test_duplicates_and_existing_members_are_conflicts(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    assert (await invite(web, admin, "dup@acme.test")).status_code == 201
    error(await invite(web, admin, "DUP@acme.test"), 409, "ALREADY_INVITED")
    error(await invite(web, admin, "a@acme.test"), 409, "ALREADY_MEMBER")
    # Joined by another route after being invited: acceptance cannot change an existing member.
    token = token_of((await invite(web, admin, "twice@acme.test", "ADMIN")).json()["link"])
    existing = await person(web, "twice@acme.test", "VIEWER", verified=True)
    error(await accept(web, existing, token), 409, "ALREADY_MEMBER")
    assert await role_in_acme(web, "twice@acme.test") == "VIEWER"


async def test_body_validation(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    for body in (
        {"email": "nobody", "role": "VIEWER"},
        {"email": "a@b.test", "role": "GOD"},
        {"email": "a b@c.test", "role": "VIEWER"},
        {"email": "x" * 250 + "@a.test", "role": "VIEWER"},
        {"role": "VIEWER"},
    ):
        response = await web.client.post("/v1/invitations", json=body, headers=admin)
        assert response.status_code == 422, body


async def test_the_open_invitation_bound_is_enforced(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    async with web.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO invitations (workspace_id, id, email, role, token_hash, expires_at) "
                "SELECT :w, gen_random_uuid(), 'bulk' || n || '@acme.test', 'VIEWER', "
                "sha256(('t' || n)::bytea), now() + interval '1000 days' "
                "FROM generate_series(1, 199) n"
            ),
            {"w": web.tenant.context.workspace_id},
        )
    assert (await invite(web, admin, "last@acme.test")).status_code == 201
    error(await invite(web, admin, "over@acme.test"), 409, "LIMIT_REACHED")


async def test_the_member_bound_is_enforced_when_joining(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    async with web.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email) SELECT gen_random_uuid(), 'm' || n || '@bulk.test' "
                "FROM generate_series(1, 498) n"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "SELECT :w, id, 'VIEWER' FROM users WHERE email LIKE '%@bulk.test'"
            ),
            {"w": web.tenant.context.workspace_id},
        )
    first = token_of((await invite(web, admin, "one@acme.test")).json()["link"])
    second = token_of((await invite(web, admin, "two@acme.test")).json()["link"])
    one = await person(web, "one@acme.test", None, verified=True)
    two = await person(web, "two@acme.test", None, verified=True)
    assert (await accept(web, one, first)).status_code == 200  # the 500th member
    error(await accept(web, two, second), 409, "LIMIT_REACHED")


# ---------------------------------------------------------------- who may touch an owner


async def test_an_admin_cannot_create_demote_or_remove_an_owner(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    admin = await person(web, "a@acme.test", "ADMIN")
    peer = await add_member(
        web.engine, web.tenant.context.workspace_id, "peer@acme.test", "DEVELOPER"
    )
    peer_id = public_id(IdKind.USER, peer.id)
    known = await members_of(web, owner)
    owner_id, admin_id = known["o@acme.test"]["user_id"], known["a@acme.test"]["user_id"]

    error(await invite(web, admin, "boss@acme.test", "OWNER"), 403, "PERMISSION_DENIED")
    to_owner = await web.client.patch(
        f"/v1/members/{peer_id}", json={"role": "OWNER"}, headers=admin
    )
    error(to_owner, 403, "PERMISSION_DENIED")
    demote = await web.client.patch(
        f"/v1/members/{owner_id}", json={"role": "VIEWER"}, headers=admin
    )
    error(demote, 403, "PERMISSION_DENIED")
    error(
        await web.client.delete(f"/v1/members/{owner_id}", headers=admin), 403, "PERMISSION_DENIED"
    )
    self_promote = await web.client.patch(
        f"/v1/members/{admin_id}", json={"role": "OWNER"}, headers=admin
    )
    error(self_promote, 403, "PERMISSION_DENIED")
    assert await role_in_acme(web, "o@acme.test") == "OWNER"
    assert await role_in_acme(web, "a@acme.test") == "ADMIN"

    # An owner may do all of it: promote a peer, invite an owner.
    promoted = await web.client.patch(
        f"/v1/members/{peer_id}", json={"role": "OWNER"}, headers=owner
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "OWNER"
    assert (await invite(web, owner, "boss@acme.test", "OWNER")).status_code == 201
    # Admins manage everyone else, themselves included (a lower role is not an owner change).
    lowered = await web.client.patch(
        f"/v1/members/{admin_id}", json={"role": "VIEWER"}, headers=admin
    )
    assert lowered.status_code == 200

    found = await rows(web)
    denied = [r for r in found if r.outcome == "denied"]
    assert [r.action for r in denied] == ["member.write_owner"] * 5
    assert {r.action for r in found if r.outcome == "allowed"} == {
        "member.update",
        "invitation.create",
    }


async def test_a_member_may_leave_under_the_same_rules(web: Api) -> None:
    await person(web, "o@acme.test", "OWNER")
    admin = await person(web, "a@acme.test", "ADMIN")
    me = (await members_of(web, admin))["a@acme.test"]["user_id"]
    assert (await web.client.delete(f"/v1/members/{me}", headers=admin)).status_code == 204
    error(await web.client.get("/v1/members", headers=admin), 404, "WORKSPACE_NOT_FOUND")
    assert await role_in_acme(web, "a@acme.test") is None


async def test_a_removal_or_demotion_takes_effect_on_the_next_request(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    dev = await person(web, "d@acme.test", "DEVELOPER")
    assert (await web.client.get("/v1/runs", headers=dev)).status_code == 200
    dev_id = (await members_of(web, owner))["d@acme.test"]["user_id"]
    await web.client.patch(f"/v1/members/{dev_id}", json={"role": "BILLING"}, headers=owner)
    error(await web.client.get("/v1/runs", headers=dev), 403, "PERMISSION_DENIED")
    await web.client.delete(f"/v1/members/{dev_id}", headers=owner)
    error(await web.client.get("/v1/runs", headers=dev), 404, "WORKSPACE_NOT_FOUND")


async def test_unknown_malformed_and_unchanged_targets(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    for bad in ("usr_01HZZZZZZZZZZZZZZZZZZZZZZZ", "nonsense", "x" * 80):
        patched = await web.client.patch(
            f"/v1/members/{bad}", json={"role": "VIEWER"}, headers=owner
        )
        error(patched, 404, "MEMBER_NOT_FOUND")
        error(await web.client.delete(f"/v1/members/{bad}", headers=owner), 404, "MEMBER_NOT_FOUND")
    me = (await members_of(web, owner))["o@acme.test"]["user_id"]
    invalid = await web.client.patch(f"/v1/members/{me}", json={"role": "GOD"}, headers=owner)
    assert invalid.status_code == 422
    same = await web.client.patch(f"/v1/members/{me}", json={"role": "OWNER"}, headers=owner)
    assert same.status_code == 200
    assert await rows(web) == []  # a no-op is not a change


# ---------------------------------------------------------------- the last owner


async def test_the_last_owner_cannot_leave_or_be_demoted(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    me = (await members_of(web, owner))["o@acme.test"]["user_id"]
    demote = await web.client.patch(f"/v1/members/{me}", json={"role": "ADMIN"}, headers=owner)
    error(demote, 409, "LAST_OWNER")
    error(await web.client.delete(f"/v1/members/{me}", headers=owner), 409, "LAST_OWNER")
    assert await role_in_acme(web, "o@acme.test") == "OWNER"
    # With a second owner, one may step down; then the other is the last.
    other = await person(web, "o2@acme.test", "OWNER")
    stepped = await web.client.patch(f"/v1/members/{me}", json={"role": "ADMIN"}, headers=owner)
    assert stepped.status_code == 200
    other_id = (await members_of(web, other))["o2@acme.test"]["user_id"]
    error(await web.client.delete(f"/v1/members/{other_id}", headers=other), 409, "LAST_OWNER")


async def test_two_owners_removing_each_other_at_once_leave_one(web: Api) -> None:
    """The FOR UPDATE on the owner rows serialises the two requests (drop it and both succeed)."""
    workspace = web.tenant.context.workspace_id
    a = await person(web, "a@acme.test", "OWNER")
    b = await person(web, "b@acme.test", "OWNER")
    ids = {email: m["user_id"] for email, m in (await members_of(web, a)).items()}
    for round_ in range(8):
        for email in ("a@acme.test", "b@acme.test"):
            if await role_in_acme(web, email) is None:
                await add_member(web.engine, workspace, email, "OWNER")
        first, second = await asyncio.gather(
            web.client.delete(f"/v1/members/{ids['b@acme.test']}", headers=a),
            web.client.delete(f"/v1/members/{ids['a@acme.test']}", headers=b),
        )
        assert sorted([first.status_code, second.status_code]) == [204, 409], round_
        async with web.engine.connect() as conn:
            owners = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM workspace_members "
                        "WHERE role = 'OWNER' AND workspace_id = :w"
                    ),
                    {"w": workspace},
                )
            ).scalar_one()
        assert owners == 1, round_


# ---------------------------------------------------------------- audit


async def test_each_administrative_change_is_one_audit_row(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    created = await invite(web, owner, "x@acme.test", "BILLING")
    inv_id = created.json()["invitation"]["id"]
    await web.client.delete(f"/v1/invitations/{inv_id}", headers=owner)
    peer = await add_member(web.engine, web.tenant.context.workspace_id, "p@acme.test", "VIEWER")
    pid = public_id(IdKind.USER, peer.id)
    await web.client.patch(f"/v1/members/{pid}", json={"role": "SECURITY"}, headers=owner)
    await web.client.delete(f"/v1/members/{pid}", headers=owner)
    found = list(reversed(await rows(web)))
    assert [(r.action, r.outcome) for r in found] == [
        ("invitation.create", "allowed"),
        ("invitation.revoke", "allowed"),
        ("member.update", "allowed"),
        ("member.remove", "allowed"),
    ]
    assert found[2].details == {"from": "VIEWER", "to": "SECURITY"}
    assert found[3].details == {"role": "SECURITY"} and found[3].resource_id == pid
    assert all(r.actor_id.startswith("user:usr_") for r in found)
    assert await rows(web, "globex") == []
