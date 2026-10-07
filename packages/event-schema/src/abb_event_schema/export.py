"""Generate the committed JSON Schema artifacts from the Python models and the registry.

    python -m abb_event_schema.export            # write schemas/<version>/
    python -m abb_event_schema.export --check    # exit 1 if the committed files are stale

The Pydantic models are the source of truth. The schema is derived so that other languages
(the TypeScript SDK, the web app, external validators) get the same contract, including the
per-type required attributes and attribute types from the registry.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from abb_event_schema import limits
from abb_event_schema.event import Event, EventIn
from abb_event_schema.registry import (
    ATTRIBUTE_KEY_PATTERN,
    EVENT_TYPES,
    KNOWN_ATTRIBUTES,
    AttrType,
    SpanRole,
)
from abb_event_schema.versioning import SCHEMA_VERSION, SUPPORTED_MAJOR

DRAFT = "https://json-schema.org/draft/2020-12/schema"
_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
_SCALAR = {"type": ["string", "number", "boolean"]}

_ATTR_JSON: dict[AttrType, dict[str, Any]] = {
    AttrType.STRING: {"type": "string"},
    AttrType.INTEGER: {"type": "integer"},
    AttrType.NUMBER: {"type": "number"},
    AttrType.BOOLEAN: {"type": "boolean"},
    AttrType.STRING_LIST: {"type": "array", "items": {"type": "string"}},
}


def _attributes_schema() -> dict[str, Any]:
    known: dict[str, Any] = {}
    for key, spec in sorted(KNOWN_ATTRIBUTES.items()):
        entry = dict(_ATTR_JSON[spec.type])
        if spec.minimum is not None:
            entry["minimum"] = spec.minimum
        known[key] = entry
    return {
        "type": "object",
        "maxProperties": limits.MAX_ATTRIBUTES,
        "propertyNames": {
            "pattern": ATTRIBUTE_KEY_PATTERN,
            "maxLength": limits.MAX_ATTRIBUTE_KEY_LENGTH,
        },
        "properties": known,
        "additionalProperties": {
            "anyOf": [
                {"type": "string", "maxLength": limits.MAX_ATTRIBUTE_STRING_LENGTH},
                {
                    "type": "integer",
                    "minimum": -limits.MAX_SAFE_INTEGER,
                    "maximum": limits.MAX_SAFE_INTEGER,
                },
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": _SCALAR, "maxItems": limits.MAX_ATTRIBUTE_LIST_ITEMS},
            ]
        },
        "default": {},
    }


def _per_type_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for spec in EVENT_TYPES.values():
        then: dict[str, Any] = {}
        if spec.required_attributes:
            then["required"] = ["attributes"]
            then["properties"] = {"attributes": {"required": sorted(spec.required_attributes)}}
        if spec.span_role in (SpanRole.OPEN, SpanRole.CLOSE):
            then.setdefault("required", []).append("span_id")
        if not then:
            continue
        rules.append(
            {
                "if": {
                    "properties": {"event_type": {"const": spec.event_type}},
                    "required": ["event_type"],
                },
                "then": then,
            }
        )
    return rules


def _build(model: type[EventIn] | type[Event], name: str) -> dict[str, Any]:
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    props = schema["properties"]
    props["attributes"] = _attributes_schema()
    props["occurred_at"] = {"type": "string", "format": "date-time", "pattern": _TIMESTAMP_PATTERN}
    if "received_at" in props:
        props["received_at"] = dict(props["occurred_at"])
    props["parent_span_id"].pop("default", None)
    props["schema_version"] = {"type": "string", "pattern": rf"^{SUPPORTED_MAJOR}\.[0-9]{{1,4}}$"}
    schema["$schema"] = DRAFT
    schema["$id"] = f"https://schemas.agentblackbox.dev/{SCHEMA_VERSION}/{name}.json"
    schema["title"] = model.__name__
    # additionalProperties is deliberately not false: consumers ignore unknown optional
    # fields (forward compatibility, spec §64.5).
    schema["allOf"] = _per_type_rules()
    schema["dependentRequired"] = {"parent_span_id": ["span_id"]}
    return schema


def event_types_document() -> dict[str, Any]:
    return {
        "$comment": "Registry of known event types. Unknown well-formed types are also accepted.",
        "schema_version": SCHEMA_VERSION,
        "event_types": [
            {
                "event_type": s.event_type,
                "class": s.event_class.value,
                "priority": s.priority.name,
                "span_role": s.span_role.value,
                "span_kind": s.span_kind.value if s.span_kind else None,
                "required_attributes": sorted(s.required_attributes),
            }
            for s in EVENT_TYPES.values()
        ],
    }


def generate() -> dict[str, dict[str, Any]]:
    return {
        "event-in.json": _build(EventIn, "event-in"),
        "event.json": _build(Event, "event"),
        "event-types.json": event_types_document(),
    }


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def default_output_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "schemas" / SCHEMA_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=default_output_dir())
    parser.add_argument("--check", action="store_true", help="fail if files are out of date")
    args = parser.parse_args(argv)

    stale = []
    for name, document in generate().items():
        target = args.out / name
        rendered = _render(document)
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != rendered:
                stale.append(str(target))
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
    if stale:
        print("stale schema files (run `make schema`):", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
