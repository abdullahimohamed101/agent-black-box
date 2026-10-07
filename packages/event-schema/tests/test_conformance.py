"""The contract's own acceptance tests: fixtures, registry coverage, schema agreement, isolation."""

import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from abb_event_schema import export
from abb_event_schema.errors import EventValidationError
from abb_event_schema.parse import parse_event_in
from abb_event_schema.registry import (
    EVENT_TYPES,
    KNOWN_ATTRIBUTES,
    classify,
    is_valid_event_type_name,
    lookup,
)

ROOT = Path(__file__).resolve().parents[1]
VALID = sorted((ROOT / "examples" / "valid").glob("*.json"))
INVALID = sorted((ROOT / "examples" / "invalid").glob("*.json"))
SCHEMA = json.loads((ROOT / "schemas" / "1.0" / "event-in.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def load(path: Path) -> Any:
    return json.loads(path.read_text())


# ---------------------------------------------------------------- fixtures


def test_every_registered_event_type_has_a_valid_example() -> None:
    assert {p.stem for p in VALID} == set(EVENT_TYPES)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_examples_pass_the_models_and_the_json_schema(path: Path) -> None:
    raw = load(path)
    assert parse_event_in(raw).event_type == path.stem
    assert not list(VALIDATOR.iter_errors(raw))


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_examples_fail_with_the_expected_code(path: Path) -> None:
    doc = load(path)
    with pytest.raises(EventValidationError) as exc:
        parse_event_in(doc["event"])
    assert exc.value.code.value == doc["expect"]["code"]
    assert doc["expect"]["issue"] in {i.code for i in exc.value.issues}


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_json_schema_agrees_where_it_can(path: Path) -> None:
    doc = load(path)
    rejected = bool(list(VALIDATOR.iter_errors(doc["event"])))
    assert rejected == doc["jsonschema_rejects"], (
        "schema and models disagree; update the fixture flag or the schema"
    )


# ---------------------------------------------------------------- registry sanity


def test_registry_is_internally_consistent() -> None:
    for name, spec in EVENT_TYPES.items():
        assert is_valid_event_type_name(name), name
        assert spec.required_attributes <= set(KNOWN_ATTRIBUTES), name
        assert lookup(name) is spec
    assert is_valid_event_type_name("custom.anything")
    assert not is_valid_event_type_name("Custom.Thing")


def test_every_started_event_has_a_completion_pair() -> None:
    for name in EVENT_TYPES:
        if name.endswith(".started") and not name.startswith("run."):
            family = name.removesuffix(".started")
            assert f"{family}.completed" in EVENT_TYPES, family


def test_classification_covers_unknown_and_custom_types() -> None:
    assert classify("llm.request.completed").value == "llm"
    assert classify("custom.my_event").value == "custom"
    assert classify("vendor.thing.happened").value == "custom"
    assert lookup("vendor.thing.happened") is None


# ---------------------------------------------------------------- generated artifacts


def test_committed_schemas_are_current() -> None:
    assert export.main(["--check"]) == 0


# ---------------------------------------------------------------- independence


def test_importing_the_contract_pulls_in_no_frameworks() -> None:
    code = (
        "import sys, abb_event_schema.parse, abb_event_schema.spans, abb_event_schema.export\n"
        "banned = ['fastapi','starlette','sqlalchemy','asyncpg','psycopg','langgraph',"
        "'langchain','openai','anthropic','httpx','requests','opentelemetry']\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603


def test_pydantic_is_the_only_runtime_dependency() -> None:
    requirements = metadata.requires("abb-event-schema") or []
    names = {re.split(r"[ ;<>=!~\[]", r, maxsplit=1)[0].lower() for r in requirements}
    assert names == {"pydantic"}


def test_readme_example_runs_without_any_framework() -> None:
    readme = (ROOT / "README.md").read_text()
    block = re.search(r"```python\n(.*?)```", readme, re.S)
    assert block, "README has no python example"
    exec(compile(block.group(1), "README.md", "exec"), {})  # noqa: S102
