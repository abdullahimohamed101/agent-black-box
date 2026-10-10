"""API key management by people: token shown once, subset-of-creator, Own revoke, bound, audit."""

import logging
from datetime import timedelta

import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.authz import actions as a
from abb_api.authz.matrix import ROLE_GRANTS
from abb_api.authz.principal import Principal
from abb_api.authz.service import ungrantable_actions
from abb_api.ids import new_uuid, public_id
from tests.api_fixtures import Api, person
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_audit import rows
from tests.test_runs_api import error


async def create(
    api: Api, headers: dict[str, str], **body: object
) -> tuple[int, dict[str, object]]:
    body.setdefault("scopes", ["runs:read"])
    response = await api.client.post("/v1/api-keys", json=body, headers=headers)
    return response.status_code, response.json()


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def user_id(api: Api, email: str) -> str:
    async with api.engine.connect() as conn:
        found = await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        return public_id(IdKind.USER, found.scalar_one())


# ---------------------------------------------------------------- creation


async def test_the_token_is_shown_once_and_the_key_works(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    dev = await person(web, "d@acme.test", "DEVELOPER")
    project = web.tenant.projects["alpha"]
    response = await web.client.post(
        "/v1/api-keys",
        json={"name": "ci", "scopes": ["events:write", "runs:read"], "project_id": project},
        headers=dev,
    )
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    body = response.json()
    token, key = body["token"], body["key"]
    assert token.startswith("abb_live_") and key["key_id"] in token
    assert key["scopes"] == ["events:write", "runs:read"] and key["project_id"] == project
    assert key["created_by"] == await user_id(web, "d@acme.test") and key["status"] == "active"

    # The key ingests, and reads what it ingested: it is a real project key.
    ids = make_run_ids()
    sent = await web.client.post(
        "/v1/events/batch",
        json={"events": [wire_event(ids, 1)]},
        headers={**bearer(token), "content-type": "application/json"},
    )
    assert sent.status_code == 202, sent.text

    # Later reads never show the token or the stored hash.
    listed = await web.client.get("/v1/api-keys", headers=dev)
    assert token not in listed.text and token.split(".")[1] not in listed.text
    assert "secret" not in listed.text
    assert key["key_id"] in [k["key_id"] for k in listed.json()["items"]]
    assert token.split(".")[1] not in caplog.text and token not in caplog.text
    async with web.engine.connect() as conn:
        stored = (
            await conn.execute(
                text("SELECT secret_hash FROM api_keys WHERE key_id = :k"), {"k": key["key_id"]}
            )
        ).scalar_one()
    assert token.split(".")[1].encode() not in bytes(stored)

    [row] = [r for r in await rows(web) if r.action == "api_key.create"]
    assert row.resource_id == key["key_id"] and row.actor_id.startswith("user:usr_")
    assert token.split(".")[1] not in str(row.details)
    assert row.details["scopes"] == ["events:write", "runs:read"]


async def test_a_revoked_key_is_refused_on_its_next_request(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    _, created = await create(web, owner, scopes=["runs:read"])
    token, key_id = created["token"], created["key"]["key_id"]  # type: ignore[index]
    assert (await web.client.get("/v1/runs", headers=bearer(token))).status_code == 200  # type: ignore[arg-type]
    assert (await web.client.delete(f"/v1/api-keys/{key_id}", headers=owner)).status_code == 204
    error(await web.client.get("/v1/runs", headers=bearer(token)), 401, "API_KEY_INVALID")  # type: ignore[arg-type]
    gone = await web.client.get("/v1/api-keys", headers=owner)
    assert key_id not in [k["key_id"] for k in gone.json()["items"]]
    error(await web.client.delete(f"/v1/api-keys/{key_id}", headers=owner), 404, "KEY_NOT_FOUND")
    assert [r.action for r in await rows(web)][:2] == ["api_key.revoke", "api_key.create"]


async def test_invalid_requests_create_nothing(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    project = web.tenant.projects["alpha"]
    foreign = web.other.projects["p"]
    random = public_id(IdKind.PROJECT, new_uuid(IdKind.PROJECT))
    cases: list[tuple[dict[str, object], int, str | None]] = [
        ({"scopes": []}, 422, None),
        ({"scopes": ["admin"]}, 422, None),
        ({"scopes": ["runs:read"], "name": ""}, 422, None),
        ({"scopes": ["runs:read"], "name": "bad\x00"}, 422, None),
        ({"scopes": ["runs:read"], "expires_in_days": 0}, 422, None),
        ({"scopes": ["runs:read"], "project_id": "nonsense"}, 422, None),
        ({"scopes": ["events:write"]}, 422, "PROJECT_REQUIRED"),
        ({"scopes": ["artifacts:write"]}, 422, "PROJECT_REQUIRED"),
        ({"scopes": ["events:write"], "project_id": foreign}, 404, "PROJECT_NOT_FOUND"),
        ({"scopes": ["runs:read"], "project_id": random}, 404, "PROJECT_NOT_FOUND"),
    ]  # fmt: skip
    for body, status, code in cases:
        got, payload = await create(web, owner, **body)
        assert got == status, body
        if code:
            assert payload["error"]["code"] == code, body  # type: ignore[index]
    assert (await create(web, owner, scopes=["runs:read"], project_id=project))[0] == 201
    async with web.engine.connect() as conn:
        count = (
            await conn.execute(text("SELECT count(*) FROM api_keys WHERE created_by IS NOT NULL"))
        ).scalar_one()
    assert count == 1
    # A foreign and a random project are indistinguishable.
    a, b = cases[8], cases[9]
    first = await web.client.post("/v1/api-keys", json=a[0], headers=owner)
    second = await web.client.post("/v1/api-keys", json=b[0], headers=owner)
    assert first.json()["error"]["message"] == second.json()["error"]["message"]
    assert first.json()["error"]["details"] == second.json()["error"]["details"]


async def test_a_key_is_never_stronger_than_its_creator() -> None:
    from abb_api.authz.matrix import role_actions

    def principal(role: str) -> Principal:
        import uuid

        return Principal(
            kind="user", workspace_id=uuid.uuid4(), project_id=None,
            actions=role_actions(role), actor_id="user:x",
        )  # fmt: skip

    runs_read = frozenset({scopes.RUNS_READ})
    assert ungrantable_actions(principal("DEVELOPER"), runs_read) == frozenset()
    # A VIEWER holds no payload.read, so a runs:read key (which implies it) would be a step up.
    assert ungrantable_actions(principal("VIEWER"), runs_read) == {a.PAYLOAD_READ, a.ARTIFACT_READ}
    assert ungrantable_actions(principal("BILLING"), runs_read) >= {a.RUN_READ, a.PAYLOAD_READ}
    # Ingestion is exempt: no person holds it, and api_key.create is what permits making such a key.
    assert (
        ungrantable_actions(principal("DEVELOPER"), frozenset({scopes.EVENTS_WRITE})) == frozenset()
    )


async def test_the_subset_rule_holds_end_to_end_if_the_matrix_ever_changes(
    web: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = await person(web, "d@acme.test", "DEVELOPER")
    weaker = ROLE_GRANTS["DEVELOPER"] - {a.PAYLOAD_READ}
    monkeypatch.setitem(ROLE_GRANTS, "DEVELOPER", weaker)
    status, payload = await create(web, dev, scopes=["runs:read"])
    assert status == 422 and payload["error"]["code"] == "SCOPE_NOT_ALLOWED"  # type: ignore[index]
    assert payload["error"]["details"]["missing_permissions"] == ["payload.read"]  # type: ignore[index]
    assert (await create(web, dev, scopes=["policy:check"]))[0] == 201  # nothing beyond the base


async def test_cookie_writes_need_the_web_origin(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    for origin in ("https://evil.test", "null", None):
        headers = {k: v for k, v in owner.items() if k != "origin"}
        if origin:
            headers["origin"] = origin
        response = await web.client.post(
            "/v1/api-keys", json={"scopes": ["runs:read"]}, headers=headers
        )
        error(response, 403, "CSRF_REJECTED")
    assert [r for r in await rows(web) if r.outcome == "allowed"] == []
    async with web.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT count(*) FROM api_keys WHERE created_by IS NOT NULL"))
        ).scalar_one() == 0


async def test_the_active_key_bound_counts_only_active_keys(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    async with web.engine.begin() as conn:
        keys = ApiKeyRepository(conn, web.tenant.context)
        active = await keys.count_active(web.clock())
        for _ in range(200 - active - 1):
            await keys.create(scopes=frozenset({scopes.RUNS_READ}))
        lapsed = await keys.create(
            scopes=frozenset({scopes.RUNS_READ}), expires_at=web.clock() - timedelta(days=1)
        )
        assert lapsed.stored.key_id  # expired keys do not count
    status, last = await create(web, owner)
    assert status == 201  # the 200th active key
    key_id = last["key"]["key_id"]  # type: ignore[index]
    error_body = await web.client.post(
        "/v1/api-keys", json={"scopes": ["runs:read"]}, headers=owner
    )
    error(error_body, 409, "LIMIT_REACHED")
    # Revoking one frees a slot.
    await web.client.delete(f"/v1/api-keys/{key_id}", headers=owner)
    assert (await create(web, owner))[0] == 201


# ---------------------------------------------------------------- revocation by role


async def test_a_developer_revokes_only_keys_they_made(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    dev = await person(web, "d@acme.test", "DEVELOPER")
    security = await person(web, "s@acme.test", "SECURITY")
    viewer = await person(web, "v@acme.test", "VIEWER")
    mine = (await create(web, dev))[1]["key"]["key_id"]  # type: ignore[index]
    theirs = (await create(web, owner))[1]["key"]["key_id"]  # type: ignore[index]
    async with web.engine.connect() as conn:
        cli_key = (
            await conn.execute(text("SELECT key_id FROM api_keys WHERE created_by IS NULL LIMIT 1"))
        ).scalar_one()

    error(await web.client.delete(f"/v1/api-keys/{theirs}", headers=dev), 403, "PERMISSION_DENIED")
    error(await web.client.delete(f"/v1/api-keys/{cli_key}", headers=dev), 403, "PERMISSION_DENIED")
    assert (await web.client.delete(f"/v1/api-keys/{mine}", headers=dev)).status_code == 204
    # Missing and foreign ids are 404 for a developer too; a viewer is stopped before any lookup.
    error(await web.client.delete("/v1/api-keys/aaaaaaaaaaaa", headers=dev), 404, "KEY_NOT_FOUND")
    error(await web.client.delete("/v1/api-keys/nonsense", headers=dev), 404, "KEY_NOT_FOUND")
    for key_id in (theirs, "aaaaaaaaaaaa", "nonsense"):
        denied = await web.client.delete(f"/v1/api-keys/{key_id}", headers=viewer)
        error(denied, 403, "PERMISSION_DENIED")
    # Security and owners revoke anyone's, including the CLI's.
    assert (await web.client.delete(f"/v1/api-keys/{theirs}", headers=security)).status_code == 204
    assert (await web.client.delete(f"/v1/api-keys/{cli_key}", headers=owner)).status_code == 204
    refused = [r for r in await rows(web) if r.outcome == "denied"]
    assert {r.action for r in refused} == {"api_key.revoke"}


async def test_listing_marks_expired_keys_and_hides_revoked_ones(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    status, made = await create(web, owner, expires_in_days=1, name="short")
    assert status == 201
    assert made["key"]["expires_at"] == (web.clock() + timedelta(days=1)).isoformat()  # type: ignore[index]
    items = (await web.client.get("/v1/api-keys", headers=owner)).json()["items"]
    by_id = {k["key_id"]: k for k in items}
    assert by_id[made["key"]["key_id"]]["status"] == "active"  # type: ignore[index]
    # Seeded fixtures: one revoked key (hidden) and one expired key (shown as expired).
    assert sum(k["status"] == "expired" for k in items) == 1
    assert len(items) == 6 + 1 + 1  # six active fixtures, one expired, the new one


async def test_other_workspaces_keys_are_invisible(web: Api) -> None:
    other_owner = await person(web, "o@globex.test", "OWNER", "globex")
    mine = await create(web, other_owner)
    assert mine[0] == 201
    acme_owner = await person(web, "o@acme.test", "OWNER")
    items = (await web.client.get("/v1/api-keys", headers=acme_owner)).json()["items"]
    assert mine[1]["key"]["key_id"] not in [k["key_id"] for k in items]  # type: ignore[index]
    error(
        await web.client.delete(f"/v1/api-keys/{mine[1]['key']['key_id']}", headers=acme_owner),  # type: ignore[index]
        404, "KEY_NOT_FOUND",
    )  # fmt: skip
