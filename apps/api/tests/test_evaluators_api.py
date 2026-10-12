"""Evaluator definitions over HTTP: validation, bounds, withholding, project scope, audit."""

from typing import Any

import pytest

from abb_api.evaluations.service import MAX_EVALUATORS
from tests.api_fixtures import Api, person
from tests.test_audit import rows

PATHS = {"allowed_globs": ["app/*.py"], "forbidden_globs": ["app/secret*"]}


async def create(api: Api, headers: dict[str, str], **body: Any) -> Any:
    body.setdefault("kind", "deterministic")
    body.setdefault("implementation", "run_succeeded")
    return await api.client.post("/v1/evaluators", json=body, headers=headers)


def error_of(response: Any) -> dict[str, Any]:
    return {**response.json()["error"], "request_id": None}


async def test_an_owner_defines_lists_and_archives_an_evaluator(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    made = await create(
        web, owner, name="paths", implementation="files_touched_only", config=PATHS,
        description="stay in app/",
    )  # fmt: skip
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["id"].startswith("evr_") and body["version"] == 1
    assert body["ground_truth"] is True and body["archived_at"] is None
    assert body["config"] == PATHS and body["config_withheld"] == []

    listed = (await web.client.get("/v1/evaluators", headers=owner)).json()["items"]
    assert [e["name"] for e in listed] == ["paths"]

    archived = await web.client.delete(f"/v1/evaluators/{body['id']}", headers=owner)
    assert archived.status_code == 204
    assert (await web.client.get("/v1/evaluators", headers=owner)).json()["items"] == []
    both = await web.client.get("/v1/evaluators?include_archived=true", headers=owner)
    assert both.json()["items"][0]["archived_at"] is not None
    again = await web.client.delete(f"/v1/evaluators/{body['id']}", headers=owner)
    assert again.status_code == 204  # idempotent


async def test_the_name_is_unique_per_workspace_and_a_slug(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    assert (await create(web, owner, name="one")).status_code == 201
    taken = await create(web, owner, name="one")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "EVALUATOR_NAME_TAKEN"
    for bad in ("Has Space", "UPPER", "", "x" * 65, "-lead"):
        assert (await create(web, owner, name=bad)).status_code == 422, bad
    # The same name in another workspace is fine.
    globex = await person(web, "g@globex.test", "OWNER", workspace="globex")
    assert (await create(web, globex, name="one")).status_code == 201


@pytest.mark.parametrize(
    ("kind", "implementation", "config"),
    [
        ("deterministic", "no_such_thing", {}),
        ("deterministic", "cost_under", {"max_usd": 1}),  # a metric under the wrong kind
        ("metric", "cost_under", {}),  # missing threshold
        ("metric", "cost_under", {"max_usd": -1}),
        ("metric", "cost_under", {"max_usd": 1, "extra": 1}),
        ("metric", "tool_error_rate_under", {"max_rate": 1.5}),
        ("metric", "retries_under", {"max": 2.5}),
        ("deterministic", "files_touched_only", {}),  # no globs at all
        ("deterministic", "files_touched_only", {"allowed_globs": ["x" * 257]}),
        ("deterministic", "files_touched_only", {"allowed_globs": ["a"] * 33}),
        (
            "deterministic",
            "files_touched_only",
            {"allowed_globs": ["a"] * 20, "forbidden_globs": ["b"] * 13},
        ),
        ("deterministic", "run_succeeded", {"anything": 1}),
        ("deterministic", "expected_files_changed", {"required_paths": []}),
        ("human", "human", {"allowed_labels": []}),
    ],
)
async def test_invalid_configuration_is_a_422_that_never_echoes_values(
    web: Api, kind: str, implementation: str, config: dict[str, Any]
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    response = await create(
        web, owner, name="bad", kind=kind, implementation=implementation, config=config
    )
    assert response.status_code == 422, response.text
    assert "x" * 20 not in response.text


async def test_a_configuration_over_the_byte_limit_is_refused_before_the_database(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    # 32 patterns of 256 four-byte characters: valid by length, far over 16 KiB as bytes.
    wide = ["\U0001d4d0" * 256 for _ in range(32)]
    response = await create(
        web, owner, name="wide", implementation="files_touched_only", config={"allowed_globs": wide}
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "EVALUATOR_INVALID"
    # 32 maximal ASCII patterns fit.
    ok = await create(
        web, owner, name="ascii", implementation="files_touched_only",
        config={"allowed_globs": ["a" * 256 for _ in range(32)]},
    )  # fmt: skip
    assert ok.status_code == 201, ok.text


async def test_a_judge_evaluator_is_refused_while_no_provider_exists(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    response = await create(
        web, owner, name="judge", kind="llm_judge", implementation="judge",
        config={"rubric": "RUBRICCANARY"},
    )  # fmt: skip
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JUDGE_NOT_CONFIGURED"
    assert "RUBRICCANARY" not in response.text


async def test_a_human_evaluator_takes_labels(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    made = await create(web, owner, name="review", kind="human", implementation="human")
    assert made.status_code == 201
    assert made.json()["config"] == {"allowed_labels": ["pass", "fail", "unsure"]}


async def test_the_workspace_holds_at_most_a_hundred_active_evaluators(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    for n in range(MAX_EVALUATORS):
        assert (await create(web, owner, name=f"e{n}")).status_code == 201
    over = await create(web, owner, name="over")
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "LIMIT_REACHED"
    assert over.json()["error"]["details"]["limit"] == "evaluators"
    # Archiving frees a slot.
    first = (await web.client.get("/v1/evaluators", headers=owner)).json()["items"][0]["id"]
    await web.client.delete(f"/v1/evaluators/{first}", headers=owner)
    assert (await create(web, owner, name="over")).status_code == 201


async def test_path_globs_and_rubrics_are_withheld_from_a_viewer(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    await create(
        web, owner, name="paths", implementation="files_touched_only",
        config={"allowed_globs": ["PATHCANARY/*"], "forbidden_globs": ["PATHCANARY2/*"]},
    )  # fmt: skip
    await create(
        web, owner, name="limit", kind="metric", implementation="cost_under",
        config={"max_usd": 2.5},
    )  # fmt: skip
    seen = (await web.client.get("/v1/evaluators", headers=viewer)).json()
    assert "PATHCANARY" not in str(seen)
    by_name = {e["name"]: e for e in seen["items"]}
    assert by_name["paths"]["config"] == {"allowed_globs": None, "forbidden_globs": None}
    assert by_name["paths"]["config_withheld"] == ["allowed_globs", "forbidden_globs"]
    assert by_name["limit"]["config"] == {"max_usd": 2.5}  # thresholds stay visible
    assert by_name["limit"]["config_withheld"] == []
    full = (await web.client.get("/v1/evaluators", headers=owner)).json()["items"]
    assert "PATHCANARY/*" in str(full)


async def test_viewers_and_billing_cannot_define_or_archive(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    made = (await create(web, owner, name="keep")).json()["id"]
    for email, role in (("v@acme.test", "VIEWER"), ("b@acme.test", "BILLING")):
        headers = await person(web, email, role)
        assert (await create(web, headers, name="nope")).status_code == 403
        gone = await web.client.delete(f"/v1/evaluators/{made}", headers=headers)
        assert gone.status_code == 403
        listed = await web.client.get("/v1/evaluators", headers=headers)
        assert listed.status_code == (403 if role == "BILLING" else 200)


async def test_a_project_key_sees_its_projects_and_workspace_wide_evaluators_only(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    alpha, beta = web.tenant.projects["alpha"], web.tenant.projects["beta"]
    await create(web, owner, name="wide")
    await create(web, owner, name="in-alpha", project_id=alpha)
    await create(web, owner, name="in-beta", project_id=beta)
    reader = await web.get("/v1/evaluators", token="reader")  # runs:read on alpha
    assert sorted(e["name"] for e in reader.json()["items"]) == ["in-alpha", "wide"]
    wide = await web.get("/v1/evaluators", token="wide_reader")
    assert len(wide.json()["items"]) == 3
    # A key cannot reach another project's evaluator by id either.
    in_beta = next(e for e in wide.json()["items"] if e["name"] == "in-beta")["id"]
    assert (await web.client.delete(f"/v1/evaluators/{in_beta}", headers=owner)).status_code == 204
    # Unknown project -> 404 PROJECT_NOT_FOUND, never an evaluator.
    missing = await create(web, owner, name="ghost", project_id="prj_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    assert missing.status_code == 404


async def test_another_workspaces_evaluator_is_a_uniform_404(web: Api) -> None:
    globex = await person(web, "g@globex.test", "OWNER", workspace="globex")
    theirs = (await create(web, globex, name="theirs")).json()["id"]
    owner = await person(web, "o@acme.test", "OWNER")
    foreign = await web.client.delete(f"/v1/evaluators/{theirs}", headers=owner)
    random = await web.client.delete("/v1/evaluators/evr_01HZZZZZZZZZZZZZZZZZZZZZZZ", headers=owner)
    malformed = await web.client.delete("/v1/evaluators/nope", headers=owner)
    assert foreign.status_code == random.status_code == malformed.status_code == 404
    assert foreign.json()["error"]["code"] == "EVALUATOR_NOT_FOUND"
    assert error_of(foreign) == error_of(random) == error_of(malformed)
    assert theirs not in foreign.text
    still = await web.client.get("/v1/evaluators", headers=globex)
    assert still.json()["items"][0]["archived_at"] is None


async def test_create_and_archive_are_audited_without_their_configuration(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    made = (
        await create(
            web,
            owner,
            name="paths",
            implementation="files_touched_only",
            config={"allowed_globs": ["AUDITPATHCANARY/*"]},
        )
    ).json()
    await web.client.delete(f"/v1/evaluators/{made['id']}", headers=owner)
    entries = [r for r in await rows(web) if r.action.startswith("evaluator.")]
    assert sorted(r.action for r in entries) == ["evaluator.archive", "evaluator.create"]
    assert "AUDITPATHCANARY" not in str([r.details for r in entries])
    assert all(r.resource_id == made["id"] for r in entries)
