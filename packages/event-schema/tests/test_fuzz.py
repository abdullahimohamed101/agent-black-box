"""Seeded mutation fuzz of the contract.

Properties: parsing only ever raises EventValidationError; anything the models accept also
satisfies the generated JSON Schema (the schema must never be stricter than the models) and
survives a serialize/parse round trip unchanged.
"""

import json
import random
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from abb_event_schema.errors import EventValidationError
from abb_event_schema.parse import dumps, parse_event_in

ROOT = Path(__file__).resolve().parents[1]
BASES = [json.loads(p.read_text()) for p in sorted((ROOT / "examples" / "valid").glob("*.json"))]
VALIDATOR = Draft202012Validator(json.loads((ROOT / "schemas/1.0/event-in.json").read_text()))
JUNK: list[Any] = [
    None, True, False, 0, -1, 1, 2**53, 2**63, 1.5, "", " ", "x", "a\x00b", "\ud800",
    "é" * 5000, "T", "1.0", "2026-10-06 20:13:22Z", "2026-10-06t20:13:22z", "2026-10-06",
    [], [1], [[1]], {}, {"a": 1}, {"a": {"b": {}}}, "evt_" + "0" * 26,
]  # fmt: skip
ATTR_KEYS = ["llm.model", "shell.exit_code", "a.b", "UP", "x" * 130, "tool.name", "k"]


def mutate(rng: random.Random, event: dict[str, Any]) -> dict[str, Any]:
    event = json.loads(json.dumps(event))
    for _ in range(rng.randint(1, 3)):
        roll = rng.random()
        if roll < 0.3:
            if not isinstance(event.get("attributes"), dict):
                event["attributes"] = {}
            event["attributes"][rng.choice(ATTR_KEYS)] = rng.choice(JUNK)
        elif roll < 0.5:
            event.pop(rng.choice(list(event)), None)
        else:
            event[rng.choice([*event, "payload", "tags", "sdk"])] = rng.choice(JUNK)
    return event


def test_mutated_events_never_crash_and_never_beat_the_schema() -> None:
    rng = random.Random(20261007)
    accepted = 0
    for _ in range(4000):
        raw = json.dumps(mutate(rng, rng.choice(BASES)))
        try:
            parsed = parse_event_in(raw)
        except EventValidationError:
            continue
        accepted += 1
        assert not list(VALIDATOR.iter_errors(json.loads(raw))), raw[:300]
        assert parse_event_in(dumps(parsed)) == parsed
    assert accepted > 100, "fuzz is not reaching the accept path"
