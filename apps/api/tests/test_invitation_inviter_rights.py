"""Demoting or removing a person revokes the open invitations they created (KI-077).

An invitation is checked against the inviter's rights when it is made; this closes the gap where
the inviter loses those rights afterwards and their links keep working.
"""

from datetime import timedelta

import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text

from abb_api.ids import public_id
from tests.api_fixtures import Api, person
from tests.test_audit import rows
from tests.test_members_api import accept, invite, token_of
from tests.test_runs_api import error

LOST = "invitation.revoke_inviter_lost_rights"


async def uid(api: Api, email: str) -> str:
    async with api.engine.connect() as conn:
        found = (
            await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).scalar_one()
    return public_id(IdKind.USER, found)


async def states(api: Api) -> dict[str, str]:
    """invitation email -> open | revoked | accepted, for acme."""
    async with api.engine.connect() as conn:
        found = await conn.execute(
            text("SELECT email, accepted_at, revoked_at FROM invitations WHERE workspace_id = :w"),
            {"w": api.tenant.context.workspace_id},
        )
        return {
            r.email: "accepted" if r.accepted_at else "revoked" if r.revoked_at else "open"
            for r in found
        }


async def setup(api: Api) -> dict[str, dict[str, str]]:
    """Two owners, two admins; each of the admins and one owner has an open invitation."""
    people = {
        "owner": await person(api, "owner@acme.test", "OWNER"),
        "owner2": await person(api, "owner2@acme.test", "OWNER"),
        "admin1": await person(api, "admin1@acme.test", "ADMIN"),
        "admin2": await person(api, "admin2@acme.test", "ADMIN"),
    }
    for who, email in (
        ("admin1", "by-admin1@x.test"),
        ("admin2", "by-admin2@x.test"),
        ("owner2", "by-owner2@x.test"),
    ):
        assert (await invite(api, people[who], email, "DEVELOPER")).status_code == 201
    return people


async def test_demoting_an_admin_revokes_their_open_invitations_but_not_anyone_elses(
    web: Api,
) -> None:
    people = await setup(web)
    target = await uid(web, "admin1@acme.test")
    r = await web.client.patch(
        f"/v1/members/{target}", json={"role": "DEVELOPER"}, headers=people["owner"]
    )
    assert r.status_code == 200
    assert await states(web) == {
        "by-admin1@x.test": "revoked",
        "by-admin2@x.test": "open",
        "by-owner2@x.test": "open",
    }
    found = [r for r in await rows(web) if r.action == LOST]
    assert len(found) == 1
    assert found[0].resource_kind == "invitation"
    assert found[0].resource_id is not None and found[0].resource_id.startswith("inv_")
    assert found[0].details == {"inviter": target, "reason": "inviter_role_changed"}
    assert found[0].actor_id.startswith("user:usr_")  # the person who made the change
    listed = (await web.client.get("/v1/invitations", headers=people["owner"])).json()["items"]
    assert sorted(i["email"] for i in listed) == ["by-admin2@x.test", "by-owner2@x.test"]


@pytest.mark.parametrize("new_role", ["DEVELOPER", "VIEWER", "SECURITY", "BILLING"])
async def test_every_role_that_cannot_invite_revokes(web: Api, new_role: str) -> None:
    people = await setup(web)
    target = await uid(web, "admin1@acme.test")
    await web.client.patch(
        f"/v1/members/{target}", json={"role": new_role}, headers=people["owner"]
    )
    assert (await states(web))["by-admin1@x.test"] == "revoked"


async def test_a_change_that_keeps_the_right_to_invite_revokes_nothing(web: Api) -> None:
    people = await setup(web)
    admin = await uid(web, "admin1@acme.test")
    owner2 = await uid(web, "owner2@acme.test")
    for target, role in ((admin, "OWNER"), (owner2, "ADMIN"), (owner2, "ADMIN"), (admin, "OWNER")):
        r = await web.client.patch(
            f"/v1/members/{target}", json={"role": role}, headers=people["owner"]
        )
        assert r.status_code == 200, r.text
    assert set((await states(web)).values()) == {"open"}
    assert [r for r in await rows(web) if r.action == LOST] == []


async def test_demoting_an_owner_revokes_the_invitations_they_made(web: Api) -> None:
    people = await setup(web)
    target = await uid(web, "owner2@acme.test")
    r = await web.client.patch(
        f"/v1/members/{target}", json={"role": "VIEWER"}, headers=people["owner"]
    )
    assert r.status_code == 200
    assert (await states(web))["by-owner2@x.test"] == "revoked"
    assert (await states(web))["by-admin1@x.test"] == "open"


async def test_removing_a_member_revokes_their_invitations_and_a_member_may_remove_themselves(
    web: Api,
) -> None:
    people = await setup(web)
    removed = await uid(web, "admin1@acme.test")
    assert (
        await web.client.delete(f"/v1/members/{removed}", headers=people["owner"])
    ).status_code == 204
    assert (await states(web))["by-admin1@x.test"] == "revoked"
    found = [r for r in await rows(web) if r.action == LOST]
    assert [r.details for r in found] == [{"inviter": removed, "reason": "inviter_removed"}]
    me = await uid(web, "admin2@acme.test")
    assert (
        await web.client.delete(f"/v1/members/{me}", headers=people["admin2"])
    ).status_code == 204
    assert (await states(web))["by-admin2@x.test"] == "revoked"
    assert (await states(web))["by-owner2@x.test"] == "open"


async def test_each_revoked_invitation_is_its_own_audit_row(web: Api) -> None:
    admin = await person(web, "admin1@acme.test", "ADMIN")
    owner = await person(web, "owner@acme.test", "OWNER")
    for i in range(3):
        await invite(web, admin, f"many{i}@x.test")
    await web.client.patch(
        f"/v1/members/{await uid(web, 'admin1@acme.test')}", json={"role": "VIEWER"}, headers=owner
    )
    found = [r for r in await rows(web) if r.action == LOST]
    assert len(found) == 3 and len({r.resource_id for r in found}) == 3


async def test_accepted_and_expired_invitations_are_left_alone(web: Api) -> None:
    owner = await person(web, "owner@acme.test", "OWNER")
    admin = await person(web, "admin1@acme.test", "ADMIN")
    token = token_of((await invite(web, admin, "joined@x.test")).json()["link"])
    joiner = await person(web, "joined@x.test", None, verified=True)
    assert (
        await accept(web, {k: v for k, v in joiner.items() if k != "x-abb-workspace"}, token)
    ).status_code == 200
    await invite(web, admin, "lapsed@x.test")
    await invite(web, admin, "live@x.test")
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE invitations SET expires_at = :t WHERE email = 'lapsed@x.test'"),
            {"t": web.clock() - timedelta(hours=1)},
        )
    target = await uid(web, "admin1@acme.test")
    await web.client.patch(f"/v1/members/{target}", json={"role": "VIEWER"}, headers=owner)
    state = await states(web)
    assert state["joined@x.test"] == "accepted"
    assert state["live@x.test"] == "revoked"
    assert state["lapsed@x.test"] == "open"  # already dead; the sweep, not this, retires it
    assert len([r for r in await rows(web) if r.action == LOST]) == 1


async def test_a_refused_change_revokes_nothing(web: Api) -> None:
    owner = await person(web, "owner@acme.test", "OWNER")
    assert (await invite(web, owner, "by-owner@x.test")).status_code == 201
    me = await uid(web, "owner@acme.test")
    r = await web.client.patch(f"/v1/members/{me}", json={"role": "VIEWER"}, headers=owner)
    assert r.status_code == 409  # the last owner
    r = await web.client.delete(f"/v1/members/{me}", headers=owner)
    assert r.status_code == 409
    assert (await states(web)) == {"by-owner@x.test": "open"}
    assert [r for r in await rows(web) if r.action == LOST] == []


async def test_a_change_to_the_same_role_revokes_nothing(web: Api) -> None:
    people = await setup(web)
    target = await uid(web, "admin1@acme.test")
    await web.client.patch(f"/v1/members/{target}", json={"role": "ADMIN"}, headers=people["owner"])
    assert (await states(web))["by-admin1@x.test"] == "open"


async def test_only_this_workspaces_invitations_are_revoked(web: Api) -> None:
    owner = await person(web, "owner@acme.test", "OWNER")
    admin = await person(web, "dual@acme.test", "ADMIN")
    elsewhere = await person(web, "dual@acme.test", "ADMIN", workspace="globex")
    assert (await invite(web, admin, "here@x.test")).status_code == 201
    assert (await invite(web, elsewhere, "there@x.test")).status_code == 201
    await web.client.patch(
        f"/v1/members/{await uid(web, 'dual@acme.test')}", json={"role": "VIEWER"}, headers=owner
    )
    async with web.engine.connect() as conn:
        found = {
            r.email: r.revoked_at is not None
            for r in await conn.execute(text("SELECT email, revoked_at FROM invitations"))
        }
    assert found == {"here@x.test": True, "there@x.test": False}


async def test_the_token_of_a_revoked_invitation_is_refused_like_an_unknown_one(web: Api) -> None:
    owner = await person(web, "owner@acme.test", "OWNER")
    admin = await person(web, "admin1@acme.test", "ADMIN")
    token = token_of((await invite(web, admin, "new@x.test")).json()["link"])
    invitee = await person(web, "new@x.test", None, verified=True)
    headers = {k: v for k, v in invitee.items() if k != "x-abb-workspace"}
    await web.client.patch(
        f"/v1/members/{await uid(web, 'admin1@acme.test')}", json={"role": "VIEWER"}, headers=owner
    )
    revoked = error(await accept(web, headers, token), 404, "INVITATION_NOT_FOUND")
    unknown = error(await accept(web, headers, "A" * 43), 404, "INVITATION_NOT_FOUND")
    assert revoked["message"] == unknown["message"] and revoked["details"] == unknown["details"]
    async with web.engine.connect() as conn:
        members = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM workspace_members m JOIN users u "
                    "ON u.id = m.user_id WHERE u.email = 'new@x.test'"
                )
            )
        ).scalar_one()
    assert members == 0
