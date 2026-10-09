"""Every route x every kind of caller, against literal expected tables (spec §91).

The tables below are a second, hand-written copy of the rules. They are compared with the running
application cell by cell, so a change to `authz/matrix.py` that is not mirrored here fails, and so
does a route without a row. Cells: allow = the success status; deny = 403; 401; 404; 400.
"""

import pytest

from tests.authz.registry import CASES
from tests.authz.world import USER_ACTORS, World, outcome

KEY_ACTORS = (
    "writer", "reader", "wide", "wide_reader", "ingest_only", "beta", "art_alpha", "art_wide",
    "other", "revoked", "expired", "none", "malformed",
)  # fmt: skip
ACTORS = (*KEY_ACTORS, *USER_ACTORS)

#            writer reader wide wide_r ingest beta art_a art_w other revoked expired none malformed
INGEST = "allow deny  deny deny   allow  allow deny  deny  allow 401 401 401 401"
LIST = "allow allow allow allow deny  allow deny  deny  allow 401 401 401 401"
# An id of acme's project alpha: another workspace's key and another project's key see a 404.
READ_ID = "allow allow allow allow deny  404   deny  deny  404   401 401 401 401"
EVERY_KEY = "allow allow allow allow allow allow allow allow allow 401 401 401 401"
NO_KEY = "deny  deny  deny  deny   deny  deny  deny  deny  deny  401 401 401 401"
UPLOAD = "deny  deny  deny  deny   deny  deny  allow deny  deny  401 401 401 401"

# People: owner admin developer viewer security billing | no_membership removed downgraded
#         expired_session revoked_session session_as_bearer key_as_cookie owner_no_header
# Nobody holds an ingestion action; a non-member and a removed member see a 404, never a 403.
U_PROJECTS = "allow allow allow     allow  allow    allow   | 404 404 allow 401 401 401 401 400"
U_PROJECT_ADMIN = (
    "allow allow deny      deny   deny     deny    | 404 404 deny  401 401 401 401 400"
)
U_NEVER = "deny  deny  deny      deny   deny     deny    | 404 404 deny  401 401 401 401 400"
U_RUNS = "allow allow allow     allow  allow    deny    | 404 404 allow 401 401 401 401 400"
U_MONEY = "allow allow allow     allow  allow    allow   | 404 404 allow 401 401 401 401 400"
U_CONTENT = "allow allow allow     deny   allow    deny    | 404 404 deny  401 401 401 401 400"

U_KEYS_READ = "allow allow allow     deny   allow    deny    | 404 404 deny  401 401 401 401 400"
U_KEYS_CREATE = "allow allow allow     deny   deny     deny    | 404 404 deny  401 401 401 401 400"
# A developer revokes keys they made (the fixture makes the key as that developer).
U_KEYS_REVOKE = U_KEYS_READ
U_ADMIN_ONLY = U_PROJECT_ADMIN  # member.write, invite.read, invite.write: owner and admin

EXPECTED: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/v1/api-keys"): (NO_KEY, U_KEYS_READ),
    ("POST", "/v1/api-keys"): (NO_KEY, U_KEYS_CREATE),
    ("DELETE", "/v1/api-keys/{key_id}"): (NO_KEY, U_KEYS_REVOKE),
    ("GET", "/v1/members"): (NO_KEY, U_MONEY),
    ("PATCH", "/v1/members/{user_id}"): (NO_KEY, U_ADMIN_ONLY),
    ("DELETE", "/v1/members/{user_id}"): (NO_KEY, U_ADMIN_ONLY),
    ("GET", "/v1/invitations"): (NO_KEY, U_ADMIN_ONLY),
    ("POST", "/v1/invitations"): (NO_KEY, U_ADMIN_ONLY),
    ("DELETE", "/v1/invitations/{invitation_id}"): (NO_KEY, U_ADMIN_ONLY),
    ("POST", "/v1/events"): (INGEST, U_NEVER),
    ("POST", "/v1/events/batch"): (INGEST, U_NEVER),
    ("POST", "/v1/runs"): (INGEST, U_NEVER),
    ("GET", "/v1/runs"): (LIST, U_RUNS),
    ("GET", "/v1/runs/{run_id}"): (READ_ID, U_RUNS),
    ("GET", "/v1/runs/{run_id}/events"): (READ_ID, U_RUNS),
    ("GET", "/v1/runs/{run_id}/events/{event_id}"): (READ_ID, U_RUNS),
    ("GET", "/v1/runs/{run_id}/spans"): (READ_ID, U_RUNS),
    ("GET", "/v1/runs/{run_id}/stream"): (READ_ID, U_RUNS),
    ("GET", "/v1/projects"): (EVERY_KEY, U_PROJECTS),
    ("POST", "/v1/projects"): (NO_KEY, U_PROJECT_ADMIN),
    ("GET", "/v1/pricing"): (LIST, U_MONEY),
    ("GET", "/v1/analytics/summary"): (LIST, U_MONEY),
    ("GET", "/v1/analytics/cost"): (LIST, U_MONEY),
    ("GET", "/v1/analytics/reliability"): (LIST, U_MONEY),
    ("GET", "/v1/analytics/performance"): (LIST, U_MONEY),
    ("PUT", "/v1/artifacts/{artifact_id}"): (UPLOAD, U_NEVER),
    ("GET", "/v1/artifacts/{artifact_id}"): (READ_ID, U_RUNS),
    ("GET", "/v1/artifacts/{artifact_id}/content"): (READ_ID, U_CONTENT),
}


def row(route: tuple[str, str]) -> list[str]:
    keys, users = EXPECTED[route]
    return [*keys.split(), *users.replace("|", " ").split()]


def expected(route: tuple[str, str], actor: str) -> str:
    cells = row(route)
    assert len(cells) == len(ACTORS), f"{route}: a row needs exactly one cell per actor"
    return cells[ACTORS.index(actor)]


def allowed_codes(actor: str, want: str) -> set[str]:
    """The right refusal per actor and outcome, not just the right status."""
    if want == "deny":
        if actor in USER_ACTORS:
            return {"PERMISSION_DENIED"}
        return {"INSUFFICIENT_SCOPE", "PROJECT_KEY_REQUIRED"}
    if want == "401":
        if actor in ("expired_session", "revoked_session", "key_as_cookie"):
            return {"SESSION_INVALID"}
        return {"API_KEY_INVALID"}
    if want == "404":
        if actor in ("no_membership", "removed_member"):
            return {"WORKSPACE_NOT_FOUND"}
        return {"RUN_NOT_FOUND", "ARTIFACT_NOT_FOUND"}
    return {"WORKSPACE_REQUIRED"}


def test_the_table_has_exactly_the_registered_routes() -> None:
    assert set(EXPECTED) == set(CASES)
    for route in EXPECTED:
        assert len(row(route)) == len(ACTORS), route
        assert set(row(route)) <= {"allow", "deny", "400", "401", "404"}, route


@pytest.mark.parametrize("actor", ACTORS)
async def test_the_actor_gets_the_expected_outcome_on_every_route(world: World, actor: str) -> None:
    problems: list[str] = []
    for route, case in CASES.items():
        response = await world.send(case, case.build(world.acme, {}), actor)
        got = outcome(case, response)
        want = expected(route, actor)
        if got != want:
            problems.append(f"{route[0]} {route[1]}: expected {want}, got {got}")
            continue
        if response.status_code >= 400 and case.transport == "asgi":
            code = response.json()["error"]["code"]
            if code not in allowed_codes(actor, want):
                problems.append(f"{route[0]} {route[1]}: unexpected error code {code}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("actor", ["writer", "none", "owner"])
async def test_head_and_options_serve_nothing_on_any_api_route(world: World, actor: str) -> None:
    """HEAD and a non-preflight OPTIONS must not become a second, unchecked way to read."""
    for route, case in CASES.items():
        if case.method != "GET":
            continue
        spec = case.build(world.acme, {})
        headers = world.headers(actor, spec)
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
    for route, (actor, scope, permission) in cases.items():
        case = CASES[route]
        response = await world.send(case, case.build(world.acme, {}), actor)
        error = response.json()["error"]
        assert response.status_code == 403 and error["code"] == "INSUFFICIENT_SCOPE", route
        assert error["details"]["required_scope"] == scope, route
        assert error["details"]["required_permission"] == permission, route


async def test_a_person_denied_is_told_the_permission_not_a_scope(world: World) -> None:
    case = CASES[("GET", "/v1/artifacts/{artifact_id}/content")]
    response = await world.send(case, case.build(world.acme, {}), "viewer")
    error = response.json()["error"]
    assert response.status_code == 403 and error["code"] == "PERMISSION_DENIED"
    assert error["details"] == {"required_permission": "payload.read"}
    # The check precedes any lookup: a viewer gets the same 403 for a missing or foreign id.
    for artifact_id in ("art_01HZZZZZZZZZZZZZZZZZZZZZZZ", world.globex.artifact_id):
        spec = case.build(world.acme, {"artifact_id": artifact_id})
        probe = await world.send(case, spec, "viewer")
        assert probe.status_code == 403 and probe.json()["error"]["code"] == "PERMISSION_DENIED"
