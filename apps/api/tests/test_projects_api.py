"""`/v1/projects`: the lookup the web uses to resolve names (KI-027), and project creation."""

import asyncio

import httpx
import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text

from abb_api.ids import new_uuid
from tests.api_fixtures import Api
from tests.auth_helpers import add_member, mint_session
from tests.conftest import make_settings
from tests.test_runs_api import error

ORIGIN = "http://localhost:3000"


@pytest.fixture
async def web(database_url: str, runtime_database_url: str, engine):  # type: ignore[no-untyped-def]
    from tests.api_fixtures import build_api

    async for api in build_api(
        database_url, engine, settings=make_settings(database_url, web_origin=ORIGIN)
    ):
        yield api


async def person(api: Api, email: str, role: str, workspace: str = "acme") -> dict[str, str]:
    tenant = api.tenant if workspace == "acme" else api.other
    user = await add_member(api.engine, tenant.context.workspace_id, email, role)
    token = await mint_session(api.engine, user.id, api.clock())
    return {
        "cookie": f"abb_session={token}",
        "origin": ORIGIN,
        "x-abb-workspace": tenant.workspace_id,
    }


async def test_a_person_lists_every_project_and_a_project_key_only_its_own(web: Api) -> None:
    viewer = await person(web, "v@acme.test", "VIEWER")
    items = (await web.client.get("/v1/projects", headers=viewer)).json()["items"]
    assert [(i["slug"], i["name"]) for i in items] == [("alpha", "alpha"), ("beta", "beta")]
    assert all(i["id"].startswith("prj_") for i in items)
    mine = (await web.get("/v1/projects", token="reader")).json()["items"]
    assert [i["slug"] for i in mine] == ["alpha"]
    wide = (await web.get("/v1/projects", token="wide_reader")).json()["items"]
    assert [i["slug"] for i in wide] == ["alpha", "beta"]
    # Another workspace's person sees only theirs.
    other = await person(web, "o@globex.test", "OWNER", "globex")
    theirs = (await web.client.get("/v1/projects", headers=other)).json()["items"]
    assert [i["slug"] for i in theirs] == ["p"]


async def test_an_admin_creates_a_project_and_everyone_sees_it(web: Api) -> None:
    admin = await person(web, "a@acme.test", "ADMIN")
    created = await web.client.post(
        "/v1/projects", json={"name": "Billing bot", "slug": "billing-bot"}, headers=admin
    )
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "billing-bot" and body["name"] == "Billing bot"
    slugs = [
        i["slug"] for i in (await web.get("/v1/projects", token="wide_reader")).json()["items"]
    ]
    assert "billing-bot" in slugs
    # The new id is usable as a project filter by a workspace-wide key.
    ok = await web.get("/v1/runs", token="wide_reader", project_id=body["id"])
    assert ok.status_code == 200


async def test_creation_validates_and_refuses_duplicates(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")

    async def post(body: dict[str, str]) -> httpx.Response:
        return await web.client.post("/v1/projects", json=body, headers=owner)

    error(await post({"name": "A", "slug": "alpha"}), 409, "PROJECT_EXISTS")
    for bad in (
        {"name": "A", "slug": "Bad Slug"},
        {"name": "A", "slug": "-lead"},
        {"name": "", "slug": "ok"},
        {"name": "x\u0000y", "slug": "ok"},
        {"name": "A" * 129, "slug": "ok"},
        {"slug": "ok"},
    ):
        assert (await post(bad)).status_code == 422, bad


async def test_a_workspace_holds_at_most_200_projects_even_under_a_race(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    workspace = web.tenant.context.workspace_id
    async with web.engine.begin() as conn:
        for i in range(197):  # alpha and beta exist: 199 in total
            await conn.execute(
                text(
                    "INSERT INTO projects (workspace_id, id, name, slug) VALUES (:w, :i, 'x', :s)"
                ),
                {"w": workspace, "i": new_uuid(IdKind.PROJECT), "s": f"filler-{i}"},
            )
    results = await asyncio.gather(
        *[
            web.client.post("/v1/projects", json={"name": "N", "slug": f"race-{i}"}, headers=owner)
            for i in range(5)
        ]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409, 409, 409, 409]
    error(next(r for r in results if r.status_code == 409), 409, "LIMIT_REACHED")
    async with web.engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM projects WHERE workspace_id = :w"), {"w": workspace}
            )
        ).scalar_one()
    assert count == 200


async def test_creating_a_project_does_not_block_foreign_key_inserts_into_the_workspace(
    web: Api,
) -> None:
    """Review F8: the creation lock must be `FOR NO KEY UPDATE`, which a key-share lock passes."""
    from sqlalchemy.exc import DBAPIError

    from abb_api.projects.repository import ProjectRepository

    async with web.engine.connect() as holder, web.engine.connect() as other:
        await holder.begin()
        await ProjectRepository(holder, web.tenant.context).lock_for_create()
        await other.begin()
        await other.execute(text("SET LOCAL lock_timeout = '500ms'"))
        try:
            # Every insert into members, keys, invitations or audit_log takes this on the workspace.
            await other.execute(
                text("SELECT 1 FROM workspaces WHERE id = :w FOR KEY SHARE"),
                {"w": web.tenant.context.workspace_id},
            )
        except DBAPIError as exc:  # pragma: no cover - the failure this test exists to catch
            pytest.fail(f"project creation blocked a key-share lock: {exc.orig}")
        finally:
            await other.rollback()
            await holder.rollback()
