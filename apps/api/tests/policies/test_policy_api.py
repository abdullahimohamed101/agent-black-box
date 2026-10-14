"""The policy management API: reading, history, diff, validation, publish and audit (ADR-140).

Who may call what is in the authorization matrix tests; this file covers behaviour for people who
may: resolution, versioning, error shape, audit rows, rate limits and that nothing echoes input.
"""

import json
from typing import Any

import httpx
import pytest

from abb_api.policies.api_service import PolicyApiService
from tests.api_fixtures import Api, person
from tests.test_audit import rows

DOC = """\
version: 1
policies:
  - id: deny-env-read
    description: Do not allow agents to read dotenv secret files.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny
"""
DOC2 = DOC.replace("decision: deny", "decision: require_approval")


async def owner(web: Api) -> dict[str, str]:
    return await person(web, "owner@acme.test", "OWNER")


async def publish(
    web: Api, headers: dict[str, str], document: str = DOC, **body: Any
) -> httpx.Response:
    return await web.client.post(
        "/v1/policies/publish", json={"document": document, **body}, headers=headers
    )


async def get(web: Api, headers: dict[str, str], path: str, **params: Any) -> httpx.Response:
    return await web.client.get(path, params=params, headers=headers)


async def policy_audit(web: Api) -> list[Any]:
    return [r for r in await rows(web) if r.action == "policy.publish"]


# ------------------------------------------------------------------------ reading


async def test_nothing_published_reads_as_the_default(web: Api) -> None:
    headers = await owner(web)
    body = (await get(web, headers, "/v1/policies/current")).json()
    assert body == {"project_id": None, "source": "default", "policy": None}
    body = (
        await get(web, headers, "/v1/policies/current", project_id=web.tenant.projects["alpha"])
    ).json()
    assert body["source"] == "default" and body["policy"] is None
    assert (await get(web, headers, "/v1/policies/versions")).json() == {
        "items": [],
        "next_before_version": None,
    }


async def test_publish_then_read_back_with_resolution(web: Api) -> None:
    headers = await owner(web)
    alpha, beta = web.tenant.projects["alpha"], web.tenant.projects["beta"]
    created = await publish(web, headers, comment="first")
    assert created.status_code == 201
    v1 = created.json()
    assert v1["version"] == 1 and v1["scope"] == "workspace" and v1["project_id"] is None
    assert v1["document"] == DOC and v1["rule_count"] == 1 and v1["comment"] == "first"
    assert v1["created_by"].startswith("user:usr_") and len(v1["document_sha256"]) == 64

    # a project with no policy of its own is governed by the workspace base
    inherited = (await get(web, headers, "/v1/policies/current", project_id=alpha)).json()
    assert inherited["source"] == "workspace" and inherited["policy"]["version"] == 1
    assert inherited["project_id"] == alpha

    own = await publish(web, headers, DOC2, project_id=alpha)
    assert own.status_code == 201 and own.json()["scope"] == "project"
    assert own.json()["project_id"] == alpha and own.json()["version"] == 1
    current = (await get(web, headers, "/v1/policies/current", project_id=alpha)).json()
    assert current["source"] == "project" and current["policy"]["document"] == DOC2
    other = (await get(web, headers, "/v1/policies/current", project_id=beta)).json()
    assert other["source"] == "workspace"
    base = (await get(web, headers, "/v1/policies/current")).json()
    assert base["source"] == "workspace" and base["policy"]["document"] == DOC


async def test_history_is_newest_first_and_pages(web: Api) -> None:
    headers = await owner(web)
    for i in range(5):
        assert (
            await publish(web, headers, DOC.replace("deny-env", f"deny-{i}"))
        ).status_code == 201
    page = (await get(web, headers, "/v1/policies/versions", limit=2)).json()
    assert [v["version"] for v in page["items"]] == [5, 4] and page["next_before_version"] == 4
    assert all("document" not in v for v in page["items"])
    older = (await get(web, headers, "/v1/policies/versions", limit=2, before_version=4)).json()
    assert [v["version"] for v in older["items"]] == [3, 2] and older["next_before_version"] == 2
    last = (await get(web, headers, "/v1/policies/versions", limit=2, before_version=2)).json()
    assert [v["version"] for v in last["items"]] == [1] and last["next_before_version"] is None


async def test_one_version_and_a_diff(web: Api) -> None:
    headers = await owner(web)
    await publish(web, headers, DOC)
    await publish(web, headers, DOC2)
    one = (await get(web, headers, "/v1/policies/versions/1")).json()
    assert one["document"] == DOC and one["version"] == 1
    assert (await get(web, headers, "/v1/policies/versions/7")).status_code == 404
    assert (await get(web, headers, "/v1/policies/versions/7")).json()["error"][
        "code"
    ] == "POLICY_VERSION_NOT_FOUND"
    diff = (await get(web, headers, "/v1/policies/diff", from_version=1, to_version=2)).json()
    assert diff["truncated"] is False and diff["from_version"] == 1
    lines = diff["diff"].splitlines()
    assert lines[:2] == ["--- v1", "+++ v2"]
    assert "-    decision: deny" in lines and "+    decision: require_approval" in lines
    same = (await get(web, headers, "/v1/policies/diff", from_version=2, to_version=2)).json()
    assert same["diff"] == ""
    missing = await get(web, headers, "/v1/policies/diff", from_version=1, to_version=9)
    assert missing.status_code == 404


async def test_a_huge_diff_is_cut(web: Api) -> None:
    headers = await owner(web)

    def rules(tag: str) -> str:
        return "version: 1\npolicies:\n" + "".join(
            f"  - id: r{i}\n    description: {tag} {'x' * 185}\n"
            "    when: {capability: a.b}\n    decision: deny\n"
            for i in range(190)
        )

    assert (await publish(web, headers, rules("one"))).status_code == 201
    assert (await publish(web, headers, rules("two"))).status_code == 201
    diff = (await get(web, headers, "/v1/policies/diff", from_version=1, to_version=2)).json()
    assert diff["truncated"] is True and len(diff["diff"].encode()) <= 64 * 1024


# ------------------------------------------------------------------------ validation


async def test_validate_reports_line_level_problems_without_side_effects(web: Api) -> None:
    headers = await owner(web)
    ok = await web.client.post("/v1/policies/validate", json={"document": DOC}, headers=headers)
    assert ok.status_code == 200
    assert ok.json() == {"valid": True, "issues": [], "rule_count": 1}
    bad = await web.client.post(
        "/v1/policies/validate",
        json={"document": "version: 1\npolicies:\n  - id: x\n    when: {}\n    decision: deny\n"},
        headers=headers,
    )
    assert bad.status_code == 200
    body = bad.json()
    assert body["valid"] is False and body["rule_count"] is None
    assert body["issues"] == [
        {
            "line": 4,
            "column": 11,
            "code": "empty_when",
            "message": "'when' needs at least one condition.",
        }
    ]
    assert not await policy_audit(web)  # validation never audits
    assert (await get(web, headers, "/v1/policies/versions")).json()["items"] == []


async def test_an_invalid_publish_is_a_422_with_issues_and_stores_and_audits_nothing(
    web: Api,
) -> None:
    headers = await owner(web)
    secret = "hunter2-AKIAIOSFODNN7EXAMPLE"
    response = await publish(web, headers, f"version: 1\ndefault: {secret}\n")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "POLICY_INVALID"
    assert error["details"]["issues"][0] == {
        "line": 2,
        "column": 10,
        "code": "invalid_default",
        "message": "'default' is allow or deny.",
    }
    assert secret not in response.text
    assert (await get(web, headers, "/v1/policies/versions")).json()["items"] == []
    assert not await policy_audit(web)


async def test_an_oversize_body_is_refused_without_echoing_it(web: Api) -> None:
    headers = await owner(web)
    sentinel = "BIG-SENTINEL-77aa"
    huge = f"version: 1\n# {sentinel}\n" + "# " + "x" * (300 * 1024)
    for path in ("/v1/policies/validate", "/v1/policies/publish"):
        response = await web.client.post(path, json={"document": huge}, headers=headers)
        assert response.status_code == 422, path
        assert sentinel not in response.text
    # between the byte cap and the character cap the parser says so with its own code
    big = "version: 1\n# " + "x" * (70 * 1024) + "\n"
    response = await web.client.post(
        "/v1/policies/validate", json={"document": big}, headers=headers
    )
    assert response.json()["issues"][0]["code"] == "document_too_large"


# ------------------------------------------------------------------------ publish


async def test_publish_writes_one_audit_row_without_the_text(web: Api) -> None:
    headers = await owner(web)
    response = await publish(web, headers, comment="secret-ish comment AKIAIOSFODNN7EXAMPLE")
    version = response.json()
    audit = await policy_audit(web)
    assert len(audit) == 1
    row = audit[0]
    assert row.actor_kind == "user" and row.outcome == "allowed"
    assert row.details == {
        "scope": "workspace",
        "version": 1,
        "sha256": version["document_sha256"],
        "rules": 1,
    }
    assert "deny-env-read" not in json.dumps(row.details)
    assert "AKIA" not in json.dumps(row.details)


async def test_base_version_conflicts_are_409_and_change_nothing(web: Api) -> None:
    headers = await owner(web)
    assert (await publish(web, headers, base_version=0)).status_code == 201
    assert (await publish(web, headers, DOC2, base_version=1)).status_code == 201
    conflict = await publish(web, headers, DOC, base_version=1)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "POLICY_VERSION_CONFLICT"
    assert conflict.json()["error"]["details"] == {"current_version": 2}
    assert len((await get(web, headers, "/v1/policies/versions")).json()["items"]) == 2


async def test_unknown_and_foreign_projects_are_the_same_404(web: Api) -> None:
    headers = await owner(web)
    foreign = web.other.projects["p"]
    from abb_event_schema.ids import IdKind, new_id

    random_id = new_id(IdKind.PROJECT)
    for path in (
        "/v1/policies/current",
        "/v1/policies/versions",
        "/v1/policies/versions/1",
    ):
        a = await get(web, headers, path, project_id=foreign)
        b = await get(web, headers, path, project_id=random_id)
        assert a.status_code == b.status_code == 404, path
        assert a.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        assert a.json()["error"]["message"] == b.json()["error"]["message"]
    for project in (foreign, random_id):
        response = await publish(web, headers, project_id=project)
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        )
    assert not await policy_audit(web)


async def test_a_workspace_never_sees_another_workspaces_policies(web: Api) -> None:
    acme = await owner(web)
    globex = await person(web, "boss@globex.test", "OWNER", workspace="globex")
    assert (await publish(web, acme, DOC2.replace("deny-env-read", "acme-only"))).status_code == 201
    assert (
        await publish(web, globex, DOC.replace("deny-env-read", "globex-only"))
    ).status_code == 201
    mine = (await get(web, acme, "/v1/policies/current")).json()["policy"]["document"]
    theirs = (await get(web, globex, "/v1/policies/current")).json()["policy"]["document"]
    assert "acme-only" in mine and "globex-only" not in mine
    assert "globex-only" in theirs and "acme-only" not in theirs
    assert len((await get(web, globex, "/v1/policies/versions")).json()["items"]) == 1


async def test_writes_are_rate_limited_per_workspace(web: Api) -> None:
    headers = await owner(web)
    web.app.state.policies = PolicyApiService(web.app.state.engine, writes_per_minute=3)
    codes = [(await publish(web, headers)).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]
    limited = await publish(web, headers)
    assert limited.headers["retry-after"].isdigit()
    assert limited.json()["error"]["details"]["retry_after_seconds"] >= 1
    # reading is not limited, and another workspace has its own budget
    assert (await get(web, headers, "/v1/policies/current")).status_code == 200
    globex = await person(web, "boss@globex.test", "OWNER", workspace="globex")
    assert (await publish(web, globex)).status_code == 201


async def test_validation_has_its_own_more_generous_limit(web: Api) -> None:
    headers = await owner(web)
    web.app.state.policies = PolicyApiService(
        web.app.state.engine, writes_per_minute=1, validations_per_minute=4
    )

    async def validate() -> int:
        response = await web.client.post(
            "/v1/policies/validate", json={"document": DOC}, headers=headers
        )
        return response.status_code

    assert [await validate() for _ in range(5)] == [200, 200, 200, 200, 429]
    assert (await publish(web, headers)).status_code == 201  # a separate budget from publishing


async def test_keys_and_non_writers_cannot_manage_policies(web: Api) -> None:
    for role, may_read in (("DEVELOPER", True), ("SECURITY", True), ("VIEWER", False)):
        headers = await person(web, f"{role.lower()}@acme.test", role)
        assert (await get(web, headers, "/v1/policies/current")).status_code == (
            200 if may_read else 403
        )
        assert (await publish(web, headers)).status_code == 403
        validate = await web.client.post(
            "/v1/policies/validate", json={"document": DOC}, headers=headers
        )
        assert validate.status_code == 403
    for token in ("reader", "wide_reader", "writer", "wide"):
        response = await web.client.get(
            "/v1/policies/current", headers={"authorization": f"Bearer {web.tokens[token]}"}
        )
        assert response.status_code == 403
        published = await web.client.post(
            "/v1/policies/publish",
            json={"document": DOC},
            headers=web.headers(token),
        )
        assert published.status_code == 403
    assert not await policy_audit(web)


async def test_a_cookie_publish_from_another_origin_is_refused(web: Api) -> None:
    headers = {**await owner(web), "origin": "https://evil.example"}
    response = await publish(web, headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_REJECTED"
    assert not await policy_audit(web)


@pytest.mark.parametrize("path", ["/v1/policies/versions/0", "/v1/policies/versions/-1"])
async def test_version_numbers_are_validated(web: Api, path: str) -> None:
    headers = await owner(web)
    response = await get(web, headers, path)
    assert response.status_code in (404, 422)
