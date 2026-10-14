"""The intent hash against the shared golden vectors (the SDK reproduces the same file)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from abb_event_schema.intent import (
    DOMAIN,
    IntentError,
    canonical_intent,
    intent_hash,
)

VECTORS = json.loads((Path(__file__).parent / "data" / "intent-hash-vectors.json").read_text())


def fields(case: dict[str, Any]) -> dict[str, Any]:
    return dict(case["intent"])


def test_the_domain_prefix_is_the_documented_one() -> None:
    assert DOMAIN == VECTORS["domain"].encode() == b"abb.intent.v1\n"


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda c: c["name"])
def test_valid_vectors_reproduce_the_canonical_bytes_and_the_hash(case: dict[str, Any]) -> None:
    canonical = canonical_intent(**fields(case))
    assert canonical.decode() == case["canonical"]
    assert intent_hash(**fields(case)) == case["sha256"]
    assert case["sha256"] == hashlib.sha256(DOMAIN + canonical).hexdigest()


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["name"])
def test_invalid_vectors_are_refused_with_the_stated_code(case: dict[str, Any]) -> None:
    with pytest.raises(IntentError) as raised:
        canonical_intent(**fields(case))
    assert raised.value.code == case["code"]
    assert "x" * 50 not in str(raised.value)  # never quotes the input


def test_normalisation_makes_composed_and_decomposed_text_one_intent() -> None:
    by_name = {c["name"]: c for c in VECTORS["valid"]}
    nfd, nfc = by_name["nfd input hashes like nfc"], by_name["nfc input"]
    assert nfd["intent"]["resource"] != nfc["intent"]["resource"]
    assert nfd["sha256"] == nfc["sha256"]


def test_every_bound_field_changes_the_hash() -> None:
    base = fields(VECTORS["valid"][1])
    reference = intent_hash(**base)
    changed = {
        "workspace_id": "ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
        "project_id": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
        "run_id": "run_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
        "agent_id": "other-agent",
        "capability": "shell.executes",
        "resource": "git2",
        "risk_class": "R4",
        "arguments": {**base["arguments"], "extra": 1},
    }
    for name, value in changed.items():
        assert intent_hash(**{**base, name: value}) != reference, name


def test_key_order_and_whitespace_in_the_input_do_not_matter() -> None:
    base = fields(VECTORS["valid"][1])
    shuffled = {**base, "arguments": dict(reversed(list(base["arguments"].items())))}
    assert intent_hash(**shuffled) == intent_hash(**base)


def test_a_boolean_is_not_an_integer_and_one_is_not_true() -> None:
    base = fields(VECTORS["valid"][0])
    assert intent_hash(**{**base, "arguments": {"a": 1}}) != intent_hash(
        **{**base, "arguments": {"a": True}}
    )
