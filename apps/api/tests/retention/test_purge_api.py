"""A project or workspace being purged is invisible and inert to the API (ADR-163).

The purge command sets `purge_state = 'purging'` first; these tests set it directly (as the owner)
and look from the outside, through the real application running as the runtime role: keys stop
working at once with the uniform 401, the project reads as not found like a deleted one, the
workspace is a 404 for its members, and nothing of it is listed. Setting it back restores
everything, which proves the column is the only switch.
"""

from sqlalchemy import text

from tests.api_fixtures import Api, person
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_runs_api import error

WRITER = "writer"


async def set_state(api: Api, table: str, where: str, state: str, **params: object) -> None:
    async with api.engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE {table} SET purge_state = :s WHERE {where}"),  # noqa: S608
            {"s": state, **params},
        )


async def alpha_run(api: Api) -> str:
    run = make_run_ids()
    sent = await api.post_batch([wire_event(run, 1)], token=WRITER)
    assert sent.status_code == 202, sent.text
    await api.drain()
    return run["run_id"]


async def test_the_keys_of_a_purging_project_stop_working_at_once(api: Api) -> None:
    run_id = await alpha_run(api)
    assert (await api.get("/v1/runs", token="reader")).status_code == 200
    alpha = api.tenant.project_uuids["alpha"]

    await set_state(api, "projects", "id = :p", "purging", p=alpha)

    # Ingestion is refused with the one uniform code, whatever the key's scope: a purging project
    # is indistinguishable from a revoked key.
    for token in (WRITER, "reader", "ingest_only"):
        refused = await api.post_batch([wire_event(make_run_ids(), 1)], token=token)
        error(refused, 401, "API_KEY_INVALID")
    error(await api.get("/v1/runs", token="reader"), 401, "API_KEY_INVALID")
    # Other projects' keys are untouched: the sibling still ingests and reads.
    ok = await api.post_batch([wire_event(make_run_ids(), 1)], token="beta")
    assert ok.status_code == 202
    assert (await api.get("/v1/runs", token="beta")).status_code == 200

    # A workspace-wide key no longer finds the project or its runs; the project is "not found".
    wide = await api.get("/v1/runs", token="wide_reader")
    assert wide.status_code == 200
    assert run_id not in [r["id"] for r in wide.json()["items"]]
    error(await api.get(f"/v1/runs/{run_id}", token="wide_reader"), 404, "RUN_NOT_FOUND")
    alpha_public = api.tenant.projects["alpha"]
    error(
        await api.get("/v1/runs", token="wide_reader", project_id=alpha_public),
        404,
        "PROJECT_NOT_FOUND",
    )
    projects = await api.get("/v1/projects", token="wide_reader")
    assert alpha_public not in [p["id"] for p in projects.json()["items"]]

    # Setting the column back restores the project (keys the throttle already saw refused stay
    # refused for its short memory, ADR-153, so this looks through a workspace-wide key).
    await set_state(api, "projects", "id = :p", "active", p=alpha)
    again = await api.get("/v1/runs", token="wide_reader")
    assert run_id in [r["id"] for r in again.json()["items"]]
    assert (await api.get(f"/v1/runs/{run_id}", token="wide_reader")).status_code == 200


async def test_a_purging_workspace_is_a_404_for_its_members_and_its_keys_are_refused(
    web: Api,
) -> None:
    dev = await person(web, "d@acme.test", "DEVELOPER")
    other = await person(web, "g@globex.test", "DEVELOPER", workspace="globex")
    assert (await web.client.get("/v1/runs", headers=dev)).status_code == 200
    me = await web.client.get("/v1/me", headers=dev)
    assert me.status_code == 200
    ws = web.tenant.context.workspace_id

    await set_state(web, "workspaces", "id = :w", "purging", w=ws)

    # A member of a purging workspace looks exactly like a non-member of an unknown one.
    error(await web.client.get("/v1/runs", headers=dev), 404, "WORKSPACE_NOT_FOUND")
    listed = await web.client.get(
        "/v1/me", headers={k: v for k, v in dev.items() if k != "x-abb-workspace"}
    )
    assert listed.status_code == 200
    assert web.tenant.workspace_id not in str(listed.json())
    # Every key of the workspace is refused, project-bound or not.
    for token in ("writer", "wide_reader", "beta"):
        error(await web.get("/v1/runs", token=token), 401, "API_KEY_INVALID")
    # Another workspace is unaffected.
    assert (await web.client.get("/v1/runs", headers=other)).status_code == 200
    assert (await web.get("/v1/runs", token="other")).status_code == 200
