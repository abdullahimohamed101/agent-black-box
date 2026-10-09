"""Every route x every kind of caller, against a literal expected table (spec §91).

The table below is a second, hand-written copy of the rules. It is compared with the running
application cell by cell, so a change to `authz/matrix.py` that is not mirrored here fails, and so
does a route without a row. Cells: allow = the success status; deny = 403; 401; 404.
"""

import pytest

from tests.authz.registry import CASES
from tests.authz.world import World, outcome

ACTORS = (
    "writer", "reader", "wide", "wide_reader", "ingest_only", "beta", "art_alpha", "art_wide",
    "other", "revoked", "expired", "none", "malformed",
)  # fmt: skip
TOKENS: dict[str, str | None] = {a: a for a in ACTORS}
TOKENS.update({"none": None, "malformed": "not-a-key"})

#            writer reader wide wide_r ingest beta art_a art_w other revoked expired none malformed
INGEST = "allow deny  deny deny   allow  allow deny  deny  allow 401 401 401 401"
LIST = "allow allow allow allow deny  allow deny  deny  allow 401 401 401 401"
# An id of acme's project alpha: another workspace's key and another project's key see a 404.
READ_ID = "allow allow allow allow deny  404   deny  deny  404   401 401 401 401"
UPLOAD = "deny  deny  deny  deny   deny  deny  allow deny  deny  401 401 401 401"

EXPECTED: dict[tuple[str, str], str] = {
    ("POST", "/v1/events"): INGEST,
    ("POST", "/v1/events/batch"): INGEST,
    ("POST", "/v1/runs"): INGEST,
    ("GET", "/v1/runs"): LIST,
    ("GET", "/v1/runs/{run_id}"): READ_ID,
    ("GET", "/v1/runs/{run_id}/events"): READ_ID,
    ("GET", "/v1/runs/{run_id}/events/{event_id}"): READ_ID,
    ("GET", "/v1/runs/{run_id}/spans"): READ_ID,
    ("GET", "/v1/runs/{run_id}/stream"): READ_ID,
    ("GET", "/v1/pricing"): LIST,
    ("GET", "/v1/analytics/summary"): LIST,
    ("GET", "/v1/analytics/cost"): LIST,
    ("GET", "/v1/analytics/reliability"): LIST,
    ("GET", "/v1/analytics/performance"): LIST,
    ("PUT", "/v1/artifacts/{artifact_id}"): UPLOAD,
    ("GET", "/v1/artifacts/{artifact_id}"): READ_ID,
    ("GET", "/v1/artifacts/{artifact_id}/content"): READ_ID,
}


def expected(route: tuple[str, str], actor: str) -> str:
    cells = EXPECTED[route].split()
    assert len(cells) == len(ACTORS), f"{route}: a row needs exactly one cell per actor"
    return cells[ACTORS.index(actor)]


def test_the_table_has_exactly_the_registered_routes() -> None:
    assert set(EXPECTED) == set(CASES)
    for route in EXPECTED:
        assert len(EXPECTED[route].split()) == len(ACTORS), route
        assert set(EXPECTED[route].split()) <= {"allow", "deny", "401", "404"}, route


@pytest.mark.parametrize("actor", ACTORS)
async def test_the_actor_gets_the_expected_outcome_on_every_route(world: World, actor: str) -> None:
    problems: list[str] = []
    for route, case in CASES.items():
        response = await world.send(case, case.build(world.acme, {}), TOKENS[actor])
        got = outcome(case, response)
        want = expected(route, actor)
        if got != want:
            problems.append(
                f"{route[0]} {route[1]}: expected {want}, got {got} ({response.status_code})"
            )
            continue
        if response.status_code >= 400 and case.transport == "asgi":
            code = response.json()["error"]["code"]
            allowed = {
                "deny": {"INSUFFICIENT_SCOPE", "PROJECT_KEY_REQUIRED"},
                "401": {"API_KEY_INVALID"},
                "404": {"RUN_NOT_FOUND", "ARTIFACT_NOT_FOUND"},
            }[want]
            if code not in allowed:
                problems.append(f"{route[0]} {route[1]}: unexpected error code {code}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("actor", ["writer", "none"])
async def test_head_and_options_serve_nothing_on_any_api_route(world: World, actor: str) -> None:
    """HEAD and a non-preflight OPTIONS must not become a second, unchecked way to read."""
    for route, case in CASES.items():
        if case.method != "GET":
            continue
        spec = case.build(world.acme, {})
        headers = world.headers(TOKENS[actor], spec)
        for method in ("HEAD", "OPTIONS"):
            response = await world.api.client.request(method, spec.path, headers=headers)
            assert response.status_code == 405, (method, route, response.status_code)
            assert world.acme.run_id not in response.text and "acme" not in response.text


async def test_insufficient_scope_still_names_the_scope_existing_clients_read(world: World) -> None:
    """SDKs and integrations read `details.required_scope`; the permission is additive."""
    cases = {
        ("POST", "/v1/events/batch"): ("reader", "events:write", "event.write"),
        ("GET", "/v1/runs"): ("ingest_only", "runs:read", "run.read"),
        ("PUT", "/v1/artifacts/{artifact_id}"): ("reader", "artifacts:write", "artifact.write"),
        ("GET", "/v1/artifacts/{artifact_id}/content"): (
            "ingest_only",
            "runs:read",
            "payload.read",
        ),
    }
    for route, (token, scope, permission) in cases.items():
        case = CASES[route]
        response = await world.send(case, case.build(world.acme, {}), token)
        error = response.json()["error"]
        assert response.status_code == 403 and error["code"] == "INSUFFICIENT_SCOPE", route
        assert error["details"]["required_scope"] == scope, route
        assert error["details"]["required_permission"] == permission, route
