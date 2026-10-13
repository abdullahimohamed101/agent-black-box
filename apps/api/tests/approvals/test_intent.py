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
