"""`GET /v1/audit`: roles, paging, `since`, isolation (D11)."""

import base64
import json
from datetime import timedelta

from abb_api.audit.repository import AuditEntry, AuditRepository
from tests.api_fixtures import Api, person
from tests.test_runs_api import error


async def seed(api: Api, count: int, tenant: str = "acme", marker: str = "n") -> None:
    context = (api.tenant if tenant == "acme" else api.other).context
    async with api.engine.begin() as conn:
        repo = AuditRepository(conn, context)
        for i in range(count):
            await repo.append(AuditEntry("cli", "cli:t", "project.create", details={marker: i}))


async def test_owner_admin_and_security_read_others_and_keys_do_not(web: Api) -> None:
    await seed(web, 2)
    for role in ("OWNER", "ADMIN", "SECURITY"):
        headers = await person(web, f"{role.lower()}@acme.test", role)
        assert (await web.client.get("/v1/audit", headers=headers)).status_code == 200
    for role in ("DEVELOPER", "VIEWER", "BILLING"):
        headers = await person(web, f"{role.lower()}@acme.test", role)
        error(await web.client.get("/v1/audit", headers=headers), 403, "PERMISSION_DENIED")
    error(await web.get("/v1/audit", token="wide_reader"), 403, "INSUFFICIENT_SCOPE")
    error(await web.client.get("/v1/audit"), 401, "API_KEY_INVALID")


async def test_paging_is_newest_first_complete_and_stable(web: Api) -> None:
    await seed(web, 7)
    headers = await person(web, "o@acme.test", "OWNER")
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": "3", **({"cursor": cursor} if cursor else {})}
        body = (await web.client.get("/v1/audit", headers=headers, params=params)).json()
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 7
    assert [int(i) for i in seen] == sorted((int(i) for i in seen), reverse=True)
    first = (await web.client.get("/v1/audit", headers=headers)).json()["items"][0]
    assert set(first) == {
        "id", "occurred_at", "actor_kind", "actor_id", "action", "outcome",
        "resource_kind", "resource_id", "details", "request_id",
    }  # fmt: skip


async def test_exactly_one_page_has_no_cursor(web: Api) -> None:
    await seed(web, 3)
    headers = await person(web, "o@acme.test", "OWNER")
    body = (await web.client.get("/v1/audit", headers=headers, params={"limit": "3"})).json()
    assert len(body["items"]) == 3 and body["next_cursor"] is None


async def test_since_filters_by_time_and_needs_a_timezone(web: Api) -> None:
    await seed(web, 2)
    headers = await person(web, "o@acme.test", "OWNER")
    future = (web.clock() + timedelta(days=365)).isoformat()
    past = "2000-01-01T00:00:00Z"
    got = (await web.client.get("/v1/audit", headers=headers, params={"since": future})).json()
    assert got["items"] == [] and got["next_cursor"] is None
    got = (await web.client.get("/v1/audit", headers=headers, params={"since": past})).json()
    assert len(got["items"]) >= 2
    error(
        await web.client.get("/v1/audit", headers=headers, params={"since": "2026-01-01T00:00:00"}),
        422, "INVALID_SINCE",
    )  # fmt: skip
    assert (
        await web.client.get("/v1/audit", headers=headers, params={"since": "garbage"})
    ).status_code == 422


async def test_hostile_cursors_and_limits_are_refused(web: Api) -> None:
    headers = await person(web, "o@acme.test", "OWNER")

    def forged(payload: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    for cursor in (
        "not-a-cursor",
        forged({"v": 1, "k": "runs", "key": [1], "m": None}),
        forged({"v": 1, "k": "audit", "key": ["1"], "m": None}),
        forged({"v": 1, "k": "audit", "key": [True], "m": None}),
        forged({"v": 1, "k": "audit", "key": [2**70], "m": None}),
        forged({"v": 1, "k": "audit", "key": [-5], "m": None}),
        forged({"v": 1, "k": "audit", "key": [1, 2], "m": None}),
    ):
        error(
            await web.client.get("/v1/audit", headers=headers, params={"cursor": cursor}),
            400, "CURSOR_INVALID",
        )  # fmt: skip
    for limit in ("0", "201", "x"):
        got = await web.client.get("/v1/audit", headers=headers, params={"limit": limit})
        assert got.status_code == 422


async def test_a_forged_cursor_cannot_cross_into_another_workspace(web: Api) -> None:
    await seed(web, 2, "acme", "mine")
    await seed(web, 3, "globex", "canary-theirs")
    headers = await person(web, "o@acme.test", "OWNER")
    cursor = base64.urlsafe_b64encode(
        json.dumps({"v": 1, "k": "audit", "key": [2**62], "m": None}).encode()
    ).decode()
    body = (await web.client.get("/v1/audit", headers=headers, params={"cursor": cursor})).json()
    assert "canary-theirs" not in json.dumps(body)
    listed = (await web.client.get("/v1/audit", headers=headers, params={"limit": "200"})).json()
    assert "canary-theirs" not in json.dumps(listed)
    assert all(i["actor_kind"] in ("cli", "user") for i in listed["items"])
