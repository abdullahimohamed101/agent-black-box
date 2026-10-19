"""The intent check: golden vectors, limits, float conversion, glob parity with the server."""

import importlib.util
import json
import random
from pathlib import Path
from typing import Any

import pytest

from agent_blackbox.policy_glob import compile_glob
from agent_blackbox.policy_intent import (
    MAX_CANONICAL_BYTES,
    IntentRejected,
    build_intent,
    canonical_text,
)

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "packages/event-schema/tests/data/intent-hash-vectors.json").read_text("utf-8")
)
SERVER_GLOB = ROOT / "apps/api/src/abb_api/policies/glob.py"


def make(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run_01J9ZZZZZZZZZZZZZZZZZZZZZC",
        "agent_id": "coding-agent",
        "capability": "shell.execute",
        "resource": "git",
        "risk_class": "R3",
        "arguments": {},
    }
    base.update(over)
    return base


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda v: v["name"])
def test_canonical_form_matches_the_golden_vectors(vector: dict[str, Any]) -> None:
    assert canonical_text(**vector["intent"]) == vector["canonical"]
    intent = vector["intent"]
    built = build_intent(
        run_id=intent["run_id"],
        agent_id=intent["agent_id"],
        capability=intent["capability"],
        resource=intent["resource"],
        risk_class=intent["risk_class"],
        arguments=intent["arguments"],
    )
    # the body the SDK sends carries exactly the fields the server hashes (minus the key's ids)
    assert set(built) == {
        "run_id", "agent_id", "capability", "resource", "risk_class", "arguments",
    }  # fmt: skip


@pytest.mark.parametrize(
    "vector",
    [v for v in VECTORS["invalid"] if not v["name"].startswith("float")],
    ids=lambda v: v["name"],
)
def test_every_invalid_vector_is_rejected_with_the_servers_code(vector: dict[str, Any]) -> None:
    intent = vector["intent"]
    with pytest.raises(IntentRejected) as info:
        build_intent(
            run_id=intent["run_id"],
            agent_id=intent["agent_id"],
            capability=intent["capability"],
            resource=intent["resource"],
            risk_class=intent["risk_class"],
            arguments=intent["arguments"],
        )
    assert info.value.code == vector["code"]


def test_floats_become_strings_and_the_rest_is_kept() -> None:
    built = build_intent(**make(arguments={"t": 0.7, "n": 3, "k": [1.5, (2, 3)], "b": True}))
    assert built["arguments"] == {"t": "0.7", "n": 3, "k": ["1.5", [2, 3]], "b": True}
    assert build_intent(**make(arguments={"x": 1.0}))["arguments"] == {"x": "1.0"}
    assert build_intent(**make(arguments={"x": float("nan")}))["arguments"] == {"x": "nan"}


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"capability": "bad cap"}, "invalid_intent"),
        ({"capability": 5}, "invalid_intent"),
        ({"capability": "a" * 129}, "invalid_intent"),
        ({"resource": 5}, "invalid_intent"),
        ({"resource": "x" * 2049}, "intent_too_large"),
        ({"risk_class": "R9"}, "invalid_intent"),
        ({"arguments": ["a"]}, "invalid_intent"),
        ({"arguments": {1: "a"}}, "invalid_intent"),
        ({"arguments": {"a": object()}}, "invalid_intent"),
        ({"arguments": {"a": b"bytes"}}, "invalid_intent"),
        ({"arguments": {"a": {"b"}}}, "invalid_intent"),
        ({"arguments": {"a": "x" * 4097}}, "intent_too_large"),
        ({"arguments": {"k" * 257: 1}}, "intent_too_large"),
        ({"arguments": {"a": 2**60}}, "invalid_intent"),
        ({"arguments": {"a": "\ud800"}}, "invalid_intent"),
        ({"arguments": {"a": [0] * 600}}, "intent_too_large"),
        ({"arguments": {"a": "z" * 4000, "b": "z" * 4000, "c": "z" * 4000, "d": "z" * 4000,
                        "e": "z" * 4000, "f": "z" * 4000, "g": "z" * 4000}}, "intent_too_large"),
    ],
)  # fmt: skip
def test_what_the_server_would_refuse_is_never_sent(over: dict[str, Any], reason: str) -> None:
    with pytest.raises(IntentRejected) as info:
        build_intent(**make(**over))
    assert info.value.reason == reason


def test_depth_is_bounded_even_for_a_cycle() -> None:
    loop: dict[str, Any] = {}
    loop["me"] = loop
    with pytest.raises(IntentRejected) as info:
        build_intent(**make(arguments=loop))
    assert info.value.reason == "intent_too_large"
    deep: Any = 1
    for _ in range(20):
        deep = [deep]
    with pytest.raises(IntentRejected):
        build_intent(**make(arguments={"d": deep}))


def _size(arguments: dict[str, Any]) -> int:
    return len(
        canonical_text(
            workspace_id="ws_" + "0" * 26,
            project_id="prj_" + "0" * 26,
            **make(arguments=arguments),
        ).encode()
    )


def test_the_size_check_is_exact_at_the_boundary() -> None:
    args: dict[str, Any] = {f"k{i}": "z" * 4096 for i in range(5)}
    args["tail"] = ""
    args["tail"] = "z" * (MAX_CANONICAL_BYTES - _size(args))
    assert _size(args) == MAX_CANONICAL_BYTES and len(args["tail"]) < 4096
    assert build_intent(**make(arguments=args))["arguments"] == args  # exactly at the limit
    args["tail"] += "z"
    with pytest.raises(IntentRejected) as info:
        build_intent(**make(arguments=args))  # one byte over
    assert info.value.code == "intent_too_large"


def test_str_subclasses_are_plain_text_in_the_body() -> None:
    class Loud(str):
        def __str__(self) -> str:
            raise RuntimeError("boom")

    built = build_intent(**make(resource=Loud("git"), arguments={"a": Loud("v")}))
    assert type(built["resource"]) is str and type(built["arguments"]["a"]) is str


# -- the glob is the server's glob -----------------------------------------------------------------


def load_server_glob() -> Any:
    spec = importlib.util.spec_from_file_location("server_glob", SERVER_GLOB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["server_glob"] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_glob_agrees_with_the_servers_on_many_patterns() -> None:
    server = load_server_glob()
    rng = random.Random(14)
    alphabet = ["a", "b", ".", "/", "*", "**", "?", "**/", ".env", "x"]
    for _ in range(600):
        pattern = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 7)))
        ours, theirs = compile_glob(pattern), server.compile_glob(pattern)
        for _ in range(12):
            text = "".join(rng.choice("ab./x") for _ in range(rng.randint(0, 8)))
            assert ours.matches(text) == theirs.matches(text), (pattern, text)
    for pattern, text, expected in [
        ("**/.env*", ".env", True),
        ("**/.env*", "a/b/.env.local", True),
        ("*.py", "a/b.py", False),
        ("src/**", "src/a/b", True),
    ]:
        assert compile_glob(pattern).matches(text) is expected


# -- attribute limits shared with the server (review P2-2) -----------------------------------------

_ATTRIBUTE_VECTORS = json.loads(
    (ROOT / "packages/event-schema/tests/data/attribute-limit-vectors.json").read_text("utf-8")
)


def _arguments_of(case: dict[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = {f"a{i:03d}": i for i in range(case["scalars"])}
    arguments.update({f"l{i:03d}": [i] for i in range(case["lists"])})
    if case["long_text"]:
        arguments["a000"] = "x" * case["long_text"]
    return arguments


@pytest.mark.parametrize("case", _ATTRIBUTE_VECTORS["cases"], ids=lambda c: c["name"])
def test_attribute_limits_match_the_server_vectors(case: dict[str, Any]) -> None:
    kwargs: dict[str, Any] = {
        "run_id": "run_" + "0" * 26,
        "agent_id": "a",
        "capability": "x.y",
        "resource": "",
        "risk_class": "R3",
        "arguments": _arguments_of(case),
    }
    if case["ok"]:
        build_intent(**kwargs)
    else:
        with pytest.raises(IntentRejected) as info:
            build_intent(**kwargs)
        assert info.value.code == case["reason"]
