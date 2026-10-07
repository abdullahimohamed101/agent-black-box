"""The API consumes the canonical contract as a dependency, never a copy."""

import json
from importlib import metadata
from pathlib import Path

from abb_event_schema import SCHEMA_VERSION
from abb_event_schema.parse import parse_event_in

from abb_api.core.config import Settings

EXAMPLE = Path(__file__).resolve().parents[3] / "packages/event-schema/examples/valid"


def test_api_depends_on_the_event_schema_package() -> None:
    requirements = metadata.requires("abb-api") or []
    assert any(r.lower().startswith("abb-event-schema") for r in requirements)


def test_the_contract_is_importable_and_usable_from_the_api() -> None:
    raw = json.loads((EXAMPLE / "tool.call.completed.json").read_text())
    assert parse_event_in(raw).event_type == "tool.call.completed"
    assert SCHEMA_VERSION == "1.0"


def test_ingestion_limit_defaults_match_the_spec() -> None:
    s = Settings(database_url="x")
    assert s.ingest_max_body_bytes == 5 * 1024 * 1024  # spec §71.4: 5 MB batch
    assert s.ingest_max_batch_events == 1000
