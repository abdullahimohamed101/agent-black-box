"""The retention policy API (ADR-159): roles, validation, audit, isolation and rate limits."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from tests.api_fixtures import WEB_ORIGIN, Api, Tick, build_api, person
from tests.conftest import make_settings
from tests.retention.helpers import NOW

MakeApi = Callable[..., Awaitable[Api]]


@pytest.fixture
async def make_api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[MakeApi]:
    generators: list[Any] = []

    async def make(**settings: Any) -> Api:
        generator = build_api(
            database_url,
            engine,
            settings=make_settings(database_url, web_origin=WEB_ORIGIN, **settings),
            clock=Tick(NOW),
        )
        generators.append(generator)
        return await generator.__anext__()

    yield make
    for generator in generators:
        await generator.aclose()


def path(api: Api, suffix: str = "") -> str:
    return f"/v1/workspaces/{api.tenant.workspace_id}/retention{suffix}"


def tiers(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["tier"]: t for t in body["tiers"]}


async def audit(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.audit_log)
            .where(t.audit_log.c.action == "retention.policy.update")
            .order_by(t.audit_log.c.id)
        )
        return list(rows)


async def test_a_new_workspace_reads_the_environment_defaults(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    response = await api.client.get(path(api), headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(x["tier"], x["days"], x["source"]) for x in body["tiers"]] == [
        ("artifacts", 30, "default"),
        ("trace", 90, "default"),
        ("run", 365, "default"),
        ("audit", 365, "default"),
    ]
    assert body["mode"] == "dry_run" and body["floor_days"] == 7
    assert body["updated_at"] is None and body["updated_by"] is None


async def test_the_defaults_follow_the_environment(make_api: MakeApi) -> None:
    api = await make_api(retention_trace_days=60, retention_audit_days="forever")
    owner = await person(api, "owner@acme.test", "OWNER")
    got = tiers((await api.client.get(path(api), headers=owner)).json())
    assert (got["trace"]["days"], got["trace"]["default_days"]) == (60, 60)
    assert (got["audit"]["days"], got["audit"]["forever"]) == (None, True)


async def test_owner_and_admin_change_the_policy_and_every_role_can_read_it(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    put = await api.client.put(path(api), json={"artifacts": 14, "audit": "forever"}, headers=owner)
    assert put.status_code == 200, put.text
    got = tiers(put.json())
    assert (got["artifacts"]["days"], got["artifacts"]["source"]) == (14, "workspace")
    assert (got["audit"]["days"], got["audit"]["forever"]) == (None, True)
    assert got["trace"]["source"] == "default"  # left out: unchanged
    assert put.json()["updated_by"].startswith("user:") and put.json()["updated_at"]
    admin = await person(api, "admin@acme.test", "ADMIN")
    assert (await api.client.put(path(api), json={"trace": 60}, headers=admin)).status_code == 200
    for role in ("OWNER", "ADMIN", "DEVELOPER", "VIEWER", "SECURITY", "BILLING"):
        headers = await person(api, f"{role.lower()}-r@acme.test", role)
        read = await api.client.get(path(api), headers=headers)
        assert read.status_code == 200, role
        assert tiers(read.json())["trace"]["days"] == 60, role


@pytest.mark.parametrize("role", ["DEVELOPER", "VIEWER", "SECURITY", "BILLING"])
async def test_other_roles_cannot_change_the_policy(make_api: MakeApi, role: str) -> None:
    api = await make_api()
    headers = await person(api, f"{role.lower()}@acme.test", role)
    response = await api.client.put(path(api), json={"trace": 60}, headers=headers)
    assert response.status_code == 403
    assert tiers((await api.client.get(path(api), headers=headers)).json())["trace"]["days"] == 90


async def test_api_keys_and_anonymous_callers_have_no_access(make_api: MakeApi) -> None:
    api = await make_api()
    for token in ("writer", "wide", "wide_reader"):
        assert (await api.get(path(api), token=token)).status_code == 403, token
    assert (await api.get(path(api), token=None)).status_code == 401


@pytest.mark.parametrize(
    ("body", "tier", "reason"),
    [
        ({"trace": 6}, "trace", "below_floor"),
        # The audit tier's own minimum is 90 days (review P2-2); "0" would collide with the "90"
        # in the message, so the number checked for repetition is 3.
        ({"audit": 3}, "audit", "below_floor"),
        ({"audit": 30}, "audit", "below_floor"),
        ({"run": -5}, "run", "below_floor"),
        ({"run": 36501}, "run", "above_ceiling"),
        ({"artifacts": 100}, "artifacts", "order"),  # the trace default is 90
        ({"trace": 366}, "trace", "order"),  # the run default is 365
        ({"trace": "forever"}, "trace", "order"),
        ({"run": 30}, "trace", "order"),  # run record shorter than trace detail (90)
    ],
)
async def test_invalid_policies_are_refused_without_repeating_the_value(
    make_api: MakeApi, body: dict[str, Any], tier: str, reason: str
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    response = await api.client.put(path(api), json=body, headers=owner)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "RETENTION_POLICY_INVALID"
    assert error["details"] == {"tier": tier, "reason": reason}
    for value in body.values():
        assert str(value) not in error["message"] or value == "forever"
    assert await audit(api.engine) == []  # nothing saved, nothing audited
    got = await api.client.get(path(api), headers=owner)
    assert all(x["source"] == "default" for x in got.json()["tiers"])


@pytest.mark.parametrize(
    "body",
    [{"trace": True}, {"trace": 7.5}, {"trace": "soon"}, {"bogus": 30}, {"trace": [30]}],
)
async def test_malformed_bodies_are_refused(make_api: MakeApi, body: dict[str, Any]) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    response = await api.client.put(path(api), json=body, headers=owner)
    assert response.status_code == 422


async def test_a_valid_combination_of_changes_is_judged_as_a_whole(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    # Each change alone would break the ordering against the defaults; together they are fine.
    ok = await api.client.put(
        path(api), json={"artifacts": 200, "trace": 300, "run": 400}, headers=owner
    )
    assert ok.status_code == 200, ok.text
    # Lowering the run record below the saved trace detail is refused.
    bad = await api.client.put(path(api), json={"run": 250}, headers=owner)
    assert bad.status_code == 422
    assert tiers((await api.client.get(path(api), headers=owner)).json())["run"]["days"] == 400


async def test_null_returns_a_tier_to_the_default_and_forever_to_a_number(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    await api.client.put(path(api), json={"audit": "forever"}, headers=owner)
    again = await api.client.put(path(api), json={"audit": 120}, headers=owner)
    assert (tiers(again.json())["audit"]["days"], tiers(again.json())["audit"]["forever"]) == (
        120,
        False,
    )
    reset = await api.client.put(path(api), json={"audit": None}, headers=owner)
    assert (tiers(reset.json())["audit"]["days"], tiers(reset.json())["audit"]["source"]) == (
        365,
        "default",
    )
    async with api.engine.connect() as conn:  # forever and days are never both stored
        row = (await conn.execute(select(t.retention_policies))).one()
    assert row.audit_days is None and row.audit_forever is False


async def test_each_changed_tier_is_audited_with_the_old_and_new_value_only(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    await api.client.put(path(api), json={"trace": 60, "audit": "forever"}, headers=owner)
    await api.client.put(path(api), json={"trace": 60}, headers=owner)  # unchanged: no row
    await api.client.put(path(api), json={"audit": None}, headers=owner)
    rows = await audit(api.engine)
    # Every row also says when the change takes effect: now for a lengthening, after the grace
    # period (24 hours) for a shortening.
    now, later = NOW.isoformat(), (NOW + timedelta(hours=24)).isoformat()
    assert [r.details for r in rows] == [
        {"tier": "trace", "old": "default", "new": 60, "effective_at": later},
        {"tier": "audit", "old": "default", "new": "forever", "effective_at": now},
        {"tier": "audit", "old": "forever", "new": "default", "effective_at": later},
    ]
    assert all(
        r.outcome == "allowed"
        and r.resource_kind == "workspace"
        and r.resource_id == api.tenant.workspace_id
        and r.actor_kind == "user"
        for r in rows
    )


async def test_a_denied_change_writes_no_policy_audit_row_and_changes_nothing(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    viewer = await person(api, "viewer@acme.test", "VIEWER")
    await api.client.put(path(api), json={"trace": 60}, headers=viewer)
    assert await audit(api.engine) == []


@pytest.mark.parametrize("suffix", ["", "/preview", "/reports"])
async def test_another_workspace_or_a_made_up_one_is_not_found(
    make_api: MakeApi, suffix: str
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    foreign = f"/v1/workspaces/{api.other.workspace_id}/retention{suffix}"
    for bad in (foreign, f"/v1/workspaces/ws_nope/retention{suffix}"):
        response = await api.client.get(bad, headers=owner)
        assert response.status_code == 404, bad
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    response = await api.client.put(
        f"/v1/workspaces/{api.other.workspace_id}/retention", json={"trace": 60}, headers=owner
    )
    assert response.status_code == 404
    async with api.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT count(*) FROM retention_policies"))
        ).scalar_one() == 0


async def test_each_workspace_has_its_own_policy(make_api: MakeApi) -> None:
    api = await make_api()
    acme = await person(api, "owner@acme.test", "OWNER")
    globex = await person(api, "owner@globex.test", "OWNER", workspace="globex")
    await api.client.put(path(api), json={"trace": 60}, headers=acme)
    other_path = f"/v1/workspaces/{api.other.workspace_id}/retention"
    assert tiers((await api.client.get(other_path, headers=globex)).json())["trace"]["days"] == 90
    await api.client.put(other_path, json={"trace": 80}, headers=globex)
    assert tiers((await api.client.get(path(api), headers=acme)).json())["trace"]["days"] == 60


async def test_policy_changes_are_rate_limited_per_workspace_with_a_closed_scope(
    make_api: MakeApi,
) -> None:
    api = await make_api(retention_writes_per_minute=2)
    owner = await person(api, "owner@acme.test", "OWNER")
    globex = await person(api, "owner@globex.test", "OWNER", workspace="globex")
    codes = [
        (await api.client.put(path(api), json={"trace": 60 + n}, headers=owner)).status_code
        for n in range(4)
    ]
    assert codes == [200, 200, 429, 429]
    limited = await api.client.put(path(api), json={"trace": 80}, headers=owner)
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1
    other = f"/v1/workspaces/{api.other.workspace_id}/retention"
    assert (await api.client.put(other, json={"trace": 60}, headers=globex)).status_code == 200
    assert (await api.client.get(path(api), headers=owner)).status_code == 200  # reads: free
    async with api.engine.connect() as conn:
        scopes = {r[0] for r in await conn.execute(text("SELECT scope FROM rate_limit_counters"))}
    assert "retention_write" in scopes


async def test_a_workspace_policy_row_is_created_lazily_and_is_the_only_write(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    await api.client.get(path(api), headers=owner)
    async with api.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT count(*) FROM retention_policies"))
        ).scalar_one() == 0
    await api.client.put(path(api), json={"trace": 60}, headers=owner)
    async with api.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT count(*) FROM retention_policies"))
        ).scalar_one() == 1
