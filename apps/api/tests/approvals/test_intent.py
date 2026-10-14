"""The server's intent hash agrees with the shared golden vectors (the SDK must too)."""

import json
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.ids import to_uuid

from abb_api.approvals.intent import IntentIn, hash_of
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "packages"
        / "event-schema"
        / "tests"
        / "data"
        / "intent-hash-vectors.json"
    ).read_text()
)


def principal(intent: dict[str, Any]) -> Principal:
    return Principal(
        kind="api_key",
        workspace_id=to_uuid(intent["workspace_id"]),
        project_id=to_uuid(intent["project_id"]),
        actions=frozenset(),
        actor_id="key:test",
    )


def body(intent: dict[str, Any]) -> IntentIn:
    keep = {k: v for k, v in intent.items() if k not in ("workspace_id", "project_id")}
    return IntentIn.model_validate(keep)


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda c: c["name"])
def test_the_api_hash_equals_the_golden_vector(case: dict[str, Any]) -> None:
    assert hash_of(principal(case["intent"]), body(case["intent"])) == case["sha256"]


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["name"])
def test_the_api_refuses_what_the_vectors_refuse(case: dict[str, Any]) -> None:
    intent = case["intent"]
    try:
        parsed = body(intent)
    except ValueError:
        return  # refused earlier by the request model (an arguments list, for example)
    with pytest.raises(AppError) as raised:
        hash_of(principal(intent), parsed)
    assert raised.value.code == "INTENT_INVALID" and raised.value.details == {
        "reason": case["code"]
    }


def test_the_hash_binds_the_workspace_and_the_project_of_the_key() -> None:
    case = VECTORS["valid"][1]
    other_workspace = {**case["intent"], "workspace_id": "ws_01J9ZZZZZZZZZZZZZZZZZZZZZB"}
    other_project = {**case["intent"], "project_id": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZA"}
    for changed in (other_workspace, other_project):
        assert hash_of(principal(changed), body(case["intent"])) != case["sha256"]


# -- attribute limits shared with the SDK (review P2-2) ---------------------------------------

ATTRIBUTE_VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "packages/event-schema/tests/data/attribute-limit-vectors.json"
    ).read_text("utf-8")
)


@pytest.mark.parametrize("case", ATTRIBUTE_VECTORS["cases"], ids=lambda c: c["name"])
async def test_attribute_limits_refuse_instead_of_truncating(api, case: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    from tests.approvals.helpers import decide, intent, publish

    await publish(api)
    arguments: dict[str, Any] = {f"a{i:03d}": i for i in range(case["scalars"])}
    arguments.update({f"l{i:03d}": [i] for i in range(case["lists"])})
    if case["long_text"]:
        arguments["a000"] = "x" * case["long_text"]
    response = await decide(api, intent(capability="docs.read", resource="x", arguments=arguments))
    if case["ok"]:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["reason"] == case["reason"]


async def test_a_rule_on_a_late_sorting_attribute_is_never_skipped_by_a_wide_intent(api) -> None:  # type: ignore[no-untyped-def]
    """The reviewer's bypass: 40 keys sorting before `force` must not hide `force` from the rule."""
    from tests.approvals.helpers import decide, intent, publish

    document = (
        "version: 1\npolicies:\n  - id: no-force\n    when: {capability: github.push, "
        "attributes: {force: true}}\n    decision: deny\n"
    )
    await publish(api, document)
    arguments: dict[str, Any] = {f"a{i:03d}": i for i in range(40)} | {"force": True}
    response = await decide(api, intent(capability="github.push", arguments=arguments))
    assert response.status_code == 200 and response.json()["decision"] == "deny"
    wide: dict[str, Any] = {f"a{i:03d}": i for i in range(70)} | {"force": True}
    refused = await decide(api, intent(capability="github.push", arguments=wide))
    assert refused.status_code == 422  # refused, never allowed with `force` unseen


def test_attributes_of_refuses_text_past_the_bound() -> None:
    from abb_api.approvals.intent import IntentIn, attributes_of
    from abb_api.core.errors import AppError

    body = IntentIn(
        run_id="run_" + "0" * 26,
        agent_id="a",
        capability="x.y",
        arguments={"k": "x" * 4097},
    )
    with pytest.raises(AppError) as info:
        attributes_of(body)
    assert info.value.details["reason"] == "attribute_too_long"
