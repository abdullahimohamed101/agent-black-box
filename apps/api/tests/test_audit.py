"""The audit log: append-only rows for admin actions, bounded rows for denials (D11, AC-8)."""

import json
import logging
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditEntry, AuditRepository, AuditRow, clean_details
from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from tests.api_fixtures import Api, person
from tests.conftest import make_settings
from tests.test_cli import invoke


async def rows(api: Api, tenant: str = "acme") -> list[AuditRow]:
    context = (api.tenant if tenant == "acme" else api.other).context
    async with api.engine.connect() as conn:
        return await AuditRepository(conn, context).recent(500)


def frozen_limiter(per_minute: int) -> InMemoryRateLimiter:
    """A bucket that never refills, so a flood has an exact expected size."""
    return InMemoryRateLimiter(
        events_per_second=per_minute / 60, burst_events=per_minute, bytes_per_second=1.0,
        burst_bytes=1, monotonic=lambda: 0.0,
    )  # fmt: skip


# ---------------------------------------------------------------- the repository


async def test_rows_are_workspace_scoped(web: Api) -> None:
    async with web.engine.begin() as conn:
        await AuditRepository(conn, web.tenant.context).append(
            AuditEntry("cli", "cli:ops", "project.create", resource_kind="project")
        )
        await AuditRepository(conn, web.other.context).append(
            AuditEntry("cli", "cli:ops", "project.create", details={"slug": "canary-slug"})
        )
    mine, theirs = await rows(web), await rows(web, "globex")
    assert [r.action for r in mine] == ["project.create"] and mine[0].details == {}
    assert theirs[0].details == {"slug": "canary-slug"}
    assert all(r.id != theirs[0].id for r in mine)


@pytest.mark.parametrize(
    "details",
    [
        {"token": "x"}, {"api_secret": "x"}, {"Cookie": "x"}, {"authorization": "x"},
        {"code": "x"}, {"state": "x"}, {"nested": {"a": 1}}, {"long": "x" * 201},
        {f"k{i}": i for i in range(21)},
        {f"k{i}": "x" * 200 for i in range(20)},  # each value fine, far over 8 KiB together
        {"k" * 65: 1},
        {"lists": [["x" * 200] * 20] * 20},
    ],
)  # fmt: skip
def test_details_refuse_secret_names_and_unbounded_values(details: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        clean_details(details)


async def test_the_largest_accepted_details_always_fit_the_database_check(web: Api) -> None:
    """Review F11: whatever `clean_details` accepts, the 8 KiB CHECK accepts (no silent drops)."""
    from abb_api.audit.repository import MAX_DETAILS_BYTES

    widest = {f"key{i:02d}": "é" * 95 for i in range(10)}  # multi-byte text, near the byte bound
    compact = json.dumps(widest, separators=(",", ":"), ensure_ascii=False)
    assert 1900 < len(compact.encode()) <= MAX_DETAILS_BYTES
    async with web.engine.begin() as conn:
        await AuditRepository(conn, web.tenant.context).append(
            AuditEntry("cli", "cli:ops", "project.create", details=widest)
        )
    assert (await rows(web))[0].details == widest


def test_details_accept_small_scalars_and_lists() -> None:
    assert clean_details({"slug": "a", "n": 3, "ok": True, "scopes": ["runs:read"]}) == {
        "slug": "a", "n": 3, "ok": True, "scopes": ["runs:read"],
    }  # fmt: skip


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome) "
        "SELECT id, 'robot', 'a', 'x', 'allowed' FROM workspaces LIMIT 1",
        "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome) "
        "SELECT id, 'cli', 'a', 'x', 'maybe' FROM workspaces LIMIT 1",
        "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome, details) "
        "SELECT id, 'cli', 'a', 'x', 'allowed', '[1]'::jsonb FROM workspaces LIMIT 1",
        "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome, details) "
        "SELECT id, 'cli', 'a', 'x', 'allowed', "
        "jsonb_build_object('k', repeat(md5(random()::text), 400)) FROM workspaces LIMIT 1",
        "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome) "
        "SELECT id, 'cli', repeat('a', 129), 'x', 'allowed' FROM workspaces LIMIT 1",
    ],
)
async def test_the_database_refuses_malformed_rows(web: Api, statement: str) -> None:
    with pytest.raises(DBAPIError, match="violates check constraint"):
        async with web.engine.begin() as conn:
            await conn.execute(text(statement))


# ---------------------------------------------------------------- denials


async def test_one_denied_request_by_a_person_is_one_row(web: Api) -> None:
    viewer = await person(web, "v@acme.test", "VIEWER")
    response = await web.client.post(
        "/v1/projects", json={"name": "N", "slug": "nope"}, headers=viewer
    )
    assert response.status_code == 403
    [row] = await rows(web)
    assert (row.actor_kind, row.outcome, row.action) == ("user", "denied", "project.write")
    assert row.actor_id.startswith("user:usr_")
    assert row.details == {"method": "POST", "route": "/v1/projects"}
    assert row.request_id == response.headers["x-request-id"]
    assert await rows(web, "globex") == []


async def test_a_flood_of_denials_writes_a_bounded_number_of_rows(web: Api) -> None:
    web.app.state.denial_limiter = frozen_limiter(10)
    viewer = await person(web, "v@acme.test", "VIEWER")
    for _ in range(60):
        response = await web.client.post(
            "/v1/projects", json={"name": "N", "slug": "nope"}, headers=viewer
        )
        assert response.status_code == 403  # always a clean 403, never a 500
    assert len(await rows(web)) == 10
    # Another person has a bucket of their own.
    other = await person(web, "b@acme.test", "BILLING")
    await web.client.post("/v1/projects", json={"name": "N", "slug": "nope"}, headers=other)
    assert len(await rows(web)) == 11


async def test_key_denials_are_logged_not_audited(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="abb_api.authz.audit")
    response = await web.client.post(
        "/v1/projects",
        json={"name": "N", "slug": "nope"},
        headers=web.headers("reader"),
    )
    assert response.status_code == 403
    assert await rows(web) == []
    line = next(r for r in caplog.records if r.getMessage() == "permission denied")
    assert line.actor_id.startswith("key:")  # type: ignore[attr-defined]


async def test_an_audit_failure_never_changes_the_response(
    web: Api, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(self: AuditRepository, entry: AuditEntry) -> None:
        raise RuntimeError("secret-looking message abb_live_xxx")

    monkeypatch.setattr(AuditRepository, "append", boom)
    viewer = await person(web, "v@acme.test", "VIEWER")
    admin = await person(web, "a@acme.test", "ADMIN")
    denied = await web.client.post("/v1/projects", json={"name": "N", "slug": "x"}, headers=viewer)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"
    created = await web.client.post("/v1/projects", json={"name": "N", "slug": "x"}, headers=admin)
    assert created.status_code == 201
    assert "abb_live_" not in caplog.text  # only the error type is logged


async def test_creating_a_project_is_one_allowed_row(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    created = await web.client.post(
        "/v1/projects", json={"name": "Bot", "slug": "bot"}, headers=admin
    )
    [row] = await rows(web)
    assert (row.action, row.outcome, row.resource_kind) == ("project.create", "allowed", "project")
    assert row.resource_id == created.json()["id"] and row.details == {"slug": "bot"}
    assert "cookie" not in json.dumps(row.details).lower()


# ---------------------------------------------------------------- the CLI


async def test_every_mutating_cli_command_writes_exactly_one_row(
    engine: AsyncEngine, database_url: str
) -> None:
    settings = make_settings(database_url)

    async def run(*argv: str) -> str:
        code, out, err = await invoke(settings, *argv)
        assert code == 0, err
        return out

    await run("create-workspace", "--name", "Acme", "--slug", "acme")
    await run("create-project", "--workspace", "acme", "--name", "Agent", "--slug", "agent")
    token = (
        await run("create-key", "--workspace", "acme", "--project", "agent", "--name", "ci")
    ).strip()
    key_id = token.removeprefix("abb_live_").split(".")[0]
    await run(
        "set-pricing-override", "--workspace", "acme", "--model-pattern", "m*",
        "--input-per-million", "1", "--output-per-million", "2",
    )  # fmt: skip
    await run("rebuild-costs", "--workspace", "acme")
    await run("revoke-key", "--workspace", "acme", "--key-id", key_id)
    await run("list-pricing", "--workspace", "acme")  # reads leave nothing behind
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT action, actor_kind, actor_id, resource_id, details::text "
                "FROM audit_log ORDER BY id"
            )
        )
        found = result.all()
    assert [r.action for r in found] == [
        "workspace.create", "project.create", "api_key.create", "pricing_override.create",
        "cost.rebuild", "api_key.revoke",
    ]  # fmt: skip
    assert {r.actor_kind for r in found} == {"cli"}
    assert all(r.actor_id.startswith("cli") for r in found)
    assert token not in json.dumps([tuple(r) for r in found])
    assert found[2].resource_id == key_id
