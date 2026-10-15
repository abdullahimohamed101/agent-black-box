"""A person's own sessions: list, revoke one, revoke the others (KI-068, Phase 19a step 5)."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.device import device_hint
from abb_api.auth.repository import SessionRepository
from abb_api.auth.service import hash_session_token
from abb_api.core.config import Settings
from tests.api_fixtures import WEB_ORIGIN, Api, build_api
from tests.auth_helpers import add_member, mint_session
from tests.conftest import make_settings

SESSIONS = "/v1/auth/sessions"


def cookie(token: str) -> dict[str, str]:
    return {"cookie": f"abb_session={token}", "origin": WEB_ORIGIN}


async def person_with_sessions(api: Api, email: str, role: str | None, count: int) -> list[str]:
    """`count` working sessions of one person (cookie values), the first being the oldest."""
    user = await add_member(api.engine, api.tenant.context.workspace_id, email, role)
    tokens = []
    for i in range(count):
        tokens.append(await mint_session(api.engine, user.id, api.clock() + timedelta(minutes=i)))
    return tokens


async def alive(api: Api, token: str) -> bool:
    r = await api.client.get("/v1/me", headers=cookie(token))
    assert r.status_code in (200, 401), r.text
    return r.status_code == 200


async def audit_actions(
    engine: AsyncEngine, workspace_id: uuid.UUID
) -> list[tuple[str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT action, details FROM audit_log WHERE workspace_id = :w "
                "AND action LIKE 'session.%' ORDER BY id"
            ),
            {"w": workspace_id},
        )
        return [(r.action, r.details) for r in rows]


# ------------------------------------------------------------------ the device label


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
            "Firefox on Linux",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like "
            "Gecko) Version/17.0 Safari/605.1.15",
            "Safari on macOS",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/129.0.0.0 "
            "Mobile Safari/537.36",
            "Chrome on Android",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "Version/17.0 Mobile/15E148 Safari/604.1",
            "Safari on iOS",
        ),
        ("curl/8.4.0", None),
        ("", None),
        (None, None),
        ("<script>alert(1)</script>" * 100, None),
    ],
)
def test_the_device_label_comes_from_a_closed_vocabulary(
    agent: str | None, expected: str | None
) -> None:
    assert device_hint(agent) == expected


def test_a_hostile_or_enormous_agent_cannot_make_an_unbounded_or_free_text_label() -> None:
    hostile = "Firefox/1 " + "A" * 1_000_000 + " Windows"  # the keyword is past the cut
    assert device_hint(hostile) == "Firefox"
    for agent in ("Firefox/1 <img src=x onerror=alert(1)> Linux", "Chrome/1\r\nX-Evil: 1 Windows"):
        hint = device_hint(agent)
        assert hint is not None and len(hint) <= 64
        assert set(hint) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ")


# ------------------------------------------------------------------ listing


async def test_a_person_lists_only_their_own_working_sessions_and_the_current_is_marked(
    web: Api,
) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "VIEWER", 3)
    theirs = await person_with_sessions(web, "bob@acme.test", "ADMIN", 2)
    async with web.engine.begin() as conn:  # a revoked one and an expired one are not listed
        await conn.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE token_hash = :h"),
            {"h": hash_session_token(mine[0])},
        )
    r = await web.client.get(SESSIONS, headers=cookie(mine[2]))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert [i["current"] for i in items] == [True, False]  # newest use first; mine[2] is current
    assert all(uuid.UUID(i["id"]) for i in items)
    assert set(items[0]) == {
        "id", "device", "created_at", "last_seen_at", "expires_at", "idle_expires_at", "current",
    }  # fmt: skip
    body = r.text
    for token in (*mine, *theirs):
        assert token not in body and hash_session_token(token).hex() not in body
    async with web.engine.connect() as conn:
        others = {
            str(x)
            for x in (
                await conn.execute(
                    text("SELECT id FROM sessions WHERE token_hash = ANY(:h)"),
                    {"h": [hash_session_token(t) for t in theirs]},
                )
            ).scalars()
        }
    assert not others & {i["id"] for i in items}


async def test_the_list_is_bounded(web: Api) -> None:
    (token,) = await person_with_sessions(web, "ada@acme.test", "VIEWER", 1)
    async with web.engine.begin() as conn:
        user_id = (
            await conn.execute(text("SELECT id FROM users WHERE email = 'ada@acme.test'"))
        ).scalar_one()
        for _ in range(120):
            await SessionRepository(conn).create(
                user_id,
                hash_session_token(uuid.uuid4().hex + "A" * 11),
                now=web.clock(),
                absolute=timedelta(hours=1),
                idle=timedelta(hours=1),
            )
    items = (await web.client.get(SESSIONS, headers=cookie(token))).json()["items"]
    assert len(items) == 100


async def test_the_device_label_of_a_login_is_stored_and_listed(web: Api) -> None:
    (token,) = await person_with_sessions(web, "ada@acme.test", "VIEWER", 1)
    async with web.engine.begin() as conn:
        await conn.execute(text("UPDATE sessions SET device_hint = 'Firefox on Linux'"))
    (item,) = (await web.client.get(SESSIONS, headers=cookie(token))).json()["items"]
    assert item["device"] == "Firefox on Linux"


# ------------------------------------------------------------------ revoking one


async def test_revoking_a_session_ends_it_at_once_and_is_audited_in_the_persons_workspaces(
    web: Api,
) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "DEVELOPER", 2)
    await add_member(  # a second workspace: the audit row lands in both
        web.engine, web.other.context.workspace_id, "ada@acme.test", "VIEWER"
    )
    listed = (await web.client.get(SESSIONS, headers=cookie(mine[1]))).json()["items"]
    other = next(i for i in listed if not i["current"])
    assert await alive(web, mine[0])
    r = await web.client.delete(f"{SESSIONS}/{other['id']}", headers=cookie(mine[1]))
    assert r.status_code == 204
    assert not await alive(web, mine[0])  # the next request, not the next sign-in
    assert await alive(web, mine[1])
    for workspace in (web.tenant.context.workspace_id, web.other.context.workspace_id):
        assert await audit_actions(web.engine, workspace) == [("session.revoke", {})]
    async with web.engine.connect() as conn:
        resource = (
            (
                await conn.execute(
                    text("SELECT resource_id FROM audit_log WHERE action='session.revoke'")
                )
            )
            .scalars()
            .all()
        )
    assert set(resource) == {other["id"]}


async def test_someone_elses_session_unknown_ids_and_ended_ones_are_the_same_404(web: Api) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "VIEWER", 2)
    theirs = await person_with_sessions(web, "bob@acme.test", "ADMIN", 1)
    async with web.engine.connect() as conn:
        foreign = (
            await conn.execute(
                text("SELECT id FROM sessions WHERE token_hash = :h"),
                {"h": hash_session_token(theirs[0])},
            )
        ).scalar_one()
    mine_listed = (await web.client.get(SESSIONS, headers=cookie(mine[1]))).json()["items"]
    ended = next(i for i in mine_listed if not i["current"])["id"]
    assert (
        await web.client.delete(f"{SESSIONS}/{ended}", headers=cookie(mine[1]))
    ).status_code == 204
    responses = [
        await web.client.delete(f"{SESSIONS}/{target}", headers=cookie(mine[1]))
        for target in (
            str(foreign),
            str(uuid.uuid4()),
            ended,  # already revoked
            "not-a-uuid",
            str(foreign).upper(),
            str(foreign).replace("-", ""),
        )
    ]
    assert {r.status_code for r in responses} == {404}
    bodies = {
        json.dumps({**r.json()["error"], "request_id": None}, sort_keys=True) for r in responses
    }
    assert len(bodies) == 1  # nothing in the body tells them apart
    assert responses[0].json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert await alive(web, theirs[0])  # and nothing of Bob's was touched
    assert await audit_actions(web.engine, web.tenant.context.workspace_id) == [
        ("session.revoke", {})
    ]  # only the one real revocation


async def test_the_session_in_use_cannot_be_revoked_this_way(web: Api) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "VIEWER", 1)
    (item,) = (await web.client.get(SESSIONS, headers=cookie(mine[0]))).json()["items"]
    r = await web.client.delete(f"{SESSIONS}/{item['id']}", headers=cookie(mine[0]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "CURRENT_SESSION"
    assert await alive(web, mine[0])


# ------------------------------------------------------------------ revoking the others


async def test_revoke_others_keeps_the_current_session_and_counts(web: Api) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "OWNER", 4)
    theirs = await person_with_sessions(web, "bob@acme.test", "ADMIN", 2)
    r = await web.client.post(f"{SESSIONS}/revoke-others", headers=cookie(mine[2]))
    assert r.status_code == 200 and r.json() == {"revoked": 3}
    assert [await alive(web, t) for t in mine] == [False, False, True, False]
    assert all([await alive(web, t) for t in theirs])
    assert await audit_actions(web.engine, web.tenant.context.workspace_id) == [
        ("session.revoke_others", {"count": 3})
    ]
    again = await web.client.post(f"{SESSIONS}/revoke-others", headers=cookie(mine[2]))
    assert again.json() == {"revoked": 0}
    assert (
        len(await audit_actions(web.engine, web.tenant.context.workspace_id)) == 1
    )  # no empty row
    listed = (await web.client.get(SESSIONS, headers=cookie(mine[2]))).json()["items"]
    assert len(listed) == 1 and listed[0]["current"]


async def test_a_person_with_no_workspace_can_manage_sessions_and_leaves_no_audit_row(
    web: Api,
) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", None, 2)
    r = await web.client.post(f"{SESSIONS}/revoke-others", headers=cookie(mine[1]))
    assert r.json() == {"revoked": 1}
    assert await audit_actions(web.engine, web.tenant.context.workspace_id) == []


# ------------------------------------------------------------------ who may call


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "DEVELOPER", "VIEWER", "SECURITY", "BILLING"])
async def test_every_role_manages_its_own_sessions(web: Api, role: str) -> None:
    mine = await person_with_sessions(web, f"{role.lower()}@acme.test", role, 2)
    assert (await web.client.get(SESSIONS, headers=cookie(mine[0]))).status_code == 200
    r = await web.client.post(f"{SESSIONS}/revoke-others", headers=cookie(mine[0]))
    assert r.status_code == 200 and r.json() == {"revoked": 1}


async def test_api_keys_and_anonymous_callers_have_no_sessions(web: Api) -> None:
    for headers in (
        web.headers("admin"),
        web.headers("reader"),
        {"authorization": "Bearer abb_live_nope"},
        {},
    ):
        for method, path in (
            ("GET", SESSIONS),
            ("POST", f"{SESSIONS}/revoke-others"),
            ("DELETE", f"{SESSIONS}/{uuid.uuid4()}"),
        ):
            r = await web.client.request(method, path, headers={**headers, "origin": WEB_ORIGIN})
            assert r.status_code == 401, (method, path, r.text)
            assert r.json()["error"]["code"] == "SESSION_INVALID"


async def test_a_session_presented_as_a_bearer_token_is_not_a_session(web: Api) -> None:
    (token,) = await person_with_sessions(web, "ada@acme.test", "VIEWER", 1)
    r = await web.client.get(SESSIONS, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_writes_need_the_web_apps_origin(web: Api) -> None:
    mine = await person_with_sessions(web, "ada@acme.test", "VIEWER", 2)
    base = {"cookie": f"abb_session={mine[0]}"}
    for origin in (None, "https://evil.example", "null"):
        headers = dict(base) | ({"origin": origin} if origin else {})
        a = await web.client.post(f"{SESSIONS}/revoke-others", headers=headers)
        b = await web.client.delete(f"{SESSIONS}/{uuid.uuid4()}", headers=headers)
        assert a.status_code == b.status_code == 403
    assert await alive(web, mine[1])


# ------------------------------------------------------------------ rate limit


@pytest.fixture
async def throttled(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings: Settings = make_settings(
        database_url, web_origin=WEB_ORIGIN, session_writes_per_minute=3
    )
    async for api in build_api(database_url, engine, settings=settings):
        yield api


async def test_session_changes_are_limited_per_person_and_reads_are_not(throttled: Api) -> None:
    api = throttled
    ada = await person_with_sessions(api, "ada@acme.test", "VIEWER", 1)
    bob = await person_with_sessions(api, "bob@acme.test", "VIEWER", 1)
    codes = []
    for _ in range(5):
        r = await api.client.post(f"{SESSIONS}/revoke-others", headers=cookie(ada[0]))
        codes.append(r.status_code)
    assert codes == [200, 200, 200, 429, 429]
    limited = await api.client.post(f"{SESSIONS}/revoke-others", headers=cookie(ada[0]))
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert (await api.client.get(SESSIONS, headers=cookie(ada[0]))).status_code == 200
    assert (  # another person's budget is their own
        await api.client.post(f"{SESSIONS}/revoke-others", headers=cookie(bob[0]))
    ).status_code == 200
