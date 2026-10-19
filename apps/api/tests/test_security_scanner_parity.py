"""The server's scanner and capture modes mean what the SDK's do (ADR-130, ADR-031).

The SDK is standard-library only (ADR-013), so the server cannot import its patterns without
making the SDK depend on a shared package; the server keeps its own bounded copy. These tests
load the SDK from the monorepo and keep the two in step: the same kinds, the same deny list, the
same output on a corpus of realistic text, and the same decisions per payload mode.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.parse import parse_event_in

from abb_api.security.redaction import DENY_KEYS, ServerRedactor
from abb_api.security.scanner import PATTERN_KINDS, ScanBudget, SecretScanner
from abb_api.security.settings import CaptureMode
from tests.ingest_helpers import RECEIVED, make_run_ids, wire_event
from tests.test_security_scanner import BENIGN_ASSIGNMENTS, ORDINARY_COMMANDS, REAL_ASSIGNMENTS

SDK_SRC = Path(__file__).resolve().parents[3] / "packages" / "sdk-python" / "src"


@pytest.fixture(scope="module")
def sdk() -> ModuleType:
    assert SDK_SRC.is_dir(), "the SDK sources are part of this repository"
    sys.path.insert(0, str(SDK_SRC))
    try:
        return importlib.import_module("agent_blackbox.redaction")
    finally:
        sys.path.remove(str(SDK_SRC))


def test_the_server_has_exactly_the_sdk_kinds(sdk: ModuleType) -> None:
    sdk_kinds = {kind for kind, _, _ in sdk._SECRET_PATTERNS}
    assert PATTERN_KINDS == sdk_kinds


def test_the_deny_keys_are_the_sdk_defaults(sdk: ModuleType) -> None:
    assert DENY_KEYS == sdk.DEFAULT_DENY_KEYS


AWS = "AKIAIOSFODNN7EXAMPLE"
CORPUS = [
    f"key {AWS} here",
    "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "curl -H 'Authorization: Bearer abcdefghijklmnop1234' https://x.io/?token=abc123",
    "password=hunter2 ghp_" + "a" * 36,
    "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY----- tail",
    '{"password": "x y z", "api_key": "k", "note": "ok"}',
    "mysql -u root -phunter2 db",
    "postgres://user:pa55@host/db and https://tok0123456789abcdefgh@github.com/o/r",
    "ключ " + AWS + " ключ",
    "Set-Cookie: sid=abc; Path=/",
    "docker login -u me -p s3cret registry.example.com",
    "npm config set //registry.npmjs.org/:_authToken npm_" + "a" * 36,
    "if password == 'letmein':",
    "git remote add o https://x-access-token:ghs_" + "b" * 36 + "@github.com/o/r.git",
    "DB_PASS=correct-horse and ROOT_PWD=toor",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "plain prose, nothing to see: the quick brown fox.",
    "total_tokens=1500 max_tokens=4096 passed=12 failed=0",
    "ran 12 tests in 0.5s\nOK\n",
    # Review P1-1 / P2-1: the ordinary and the secret-shaped corpora of the scanner tests mean the
    # same to both sides, so the server never rewrites what the SDK keeps (and the reverse).
    *ORDINARY_COMMANDS,
    *BENIGN_ASSIGNMENTS,
    *(text for text, _ in REAL_ASSIGNMENTS),
    "curl -u alice:s3cret https://x.example",
    "docker run --user 1000:1000 image",
    '{"password": "[REDACTED]"}',
]


@pytest.mark.parametrize("text", CORPUS)
def test_the_server_redacts_the_sdk_corpus_the_same_way(sdk: ModuleType, text: str) -> None:
    expected = sdk.Redactor().redact_text(text)
    got = SecretScanner().scan_text(text, ScanBudget(10**6), unit_limit=10**6).text
    assert got == expected


# ------------------------------------------------------------------- payload modes


def sdk_event(**extra: Any) -> dict[str, Any]:
    return wire_event(
        make_run_ids(),
        1,
        attributes={
            "tool.name": "t",
            "error.message": "boom",
            "metadata.owner": "alice",
            "shell.command": "ls",
        },
        payload={"k": "v"},
        payload_ref="artifact://art_01J90000000000000000000031",
        **extra,
    )


def server_event(raw: dict[str, Any]) -> Event:
    return finalize(
        parse_event_in(raw),
        workspace_id=new_id(IdKind.WORKSPACE),
        project_id=new_id(IdKind.PROJECT),
        received_at=RECEIVED,
    )


@pytest.mark.parametrize(
    ("sdk_mode", "mode"),
    [("FULL", CaptureMode.FULL), ("METADATA_ONLY", CaptureMode.METADATA_ONLY),
     ("DISABLED", CaptureMode.DISABLED)],
)  # fmt: skip
def test_each_capture_mode_keeps_what_the_sdk_mode_keeps(
    sdk: ModuleType, sdk_mode: str, mode: CaptureMode
) -> None:
    raw = sdk_event()
    sdk_out = sdk.Redactor(payload_mode=sdk.PayloadMode[sdk_mode]).apply(
        {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
    )
    server_out = ServerRedactor().apply(server_event(raw), mode)

    def survivors(attributes: dict[str, Any], payload: Any, ref: Any) -> set[str]:
        kept = {k for k in ("tool.name", "error.message", "metadata.owner", "shell.command")
                if k in attributes}  # fmt: skip
        return kept | ({"payload"} if payload else set()) | ({"payload_ref"} if ref else set())

    assert survivors(
        sdk_out["attributes"], sdk_out.get("payload"), sdk_out.get("payload_ref")
    ) == survivors(server_out.attributes, server_out.payload, server_out.payload_ref)


# ---------------------------------------------- every attribute, not four (review P3-10)


def _sample(spec: Any) -> Any:
    from abb_event_schema.registry import AttrType

    return {
        AttrType.STRING: "value",
        AttrType.INTEGER: int(spec.minimum or 1),
        AttrType.NUMBER: float(spec.minimum or 1.5),
        AttrType.BOOLEAN: True,
        AttrType.STRING_LIST: ["value"],
    }[spec.type]


def every_attribute() -> dict[str, Any]:
    """Each attribute the schema knows, with a valid value, plus keys it does not know (they are
    preserved untouched) and run metadata. A change to which attributes the SDK's `apply` treats
    specially, or one the server does not mirror, fails here, whichever attribute it is."""
    from abb_event_schema.registry import KNOWN_ATTRIBUTES

    attributes = {key: _sample(spec) for key, spec in KNOWN_ATTRIBUTES.items()}
    attributes.update(
        {
            "error.message": "boom",
            "metadata.owner": "alice",
            "metadata.ticket.id": "T-1",
            "metadata": "bare",  # the prefix itself is not run metadata
            "custom.note": "free text",
            "x.error.message": "not the error message",
        }
    )
    return attributes


def attribute_chunks(size: int = 40) -> list[dict[str, Any]]:
    items = sorted(every_attribute().items())
    # `tool.name` is required by the event type the chunks are sent as, so every chunk has it
    return [{**dict(items[i : i + size]), "tool.name": "t"} for i in range(0, len(items), size)]


@pytest.mark.parametrize(
    ("sdk_mode", "mode"),
    [("FULL", CaptureMode.FULL), ("METADATA_ONLY", CaptureMode.METADATA_ONLY),
     ("DISABLED", CaptureMode.DISABLED)],
)  # fmt: skip
def test_every_attribute_is_kept_or_dropped_as_the_sdk_does(
    sdk: ModuleType, sdk_mode: str, mode: CaptureMode
) -> None:
    from abb_event_schema.registry import (
        PAYLOAD_REFERENCE_ATTRIBUTES,
        SERVER_REDACTION_ATTRIBUTES,
    )

    seen: set[str] = set()
    for chunk in attribute_chunks():
        raw = wire_event(make_run_ids(), 1, attributes=dict(chunk))
        sdk_out = sdk.Redactor(payload_mode=sdk.PayloadMode[sdk_mode]).apply(
            {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
        )
        server_out = ServerRedactor().apply(server_event(raw), mode)
        # `redaction.*` is the server's: it discards a client's, so those keys are not compared
        sdk_kept = set(sdk_out["attributes"]) - SERVER_REDACTION_ATTRIBUTES
        server_kept = set(server_out.attributes) - SERVER_REDACTION_ATTRIBUTES
        # The one deliberate difference (ADR-130 §3): outside FULL the server also drops the
        # attributes that point at a stored artifact, which the SDK leaves to its own artifact path.
        pointers = set(chunk) & PAYLOAD_REFERENCE_ATTRIBUTES
        if mode is not CaptureMode.FULL:
            assert not (server_kept & pointers)
            server_kept |= pointers & sdk_kept
        assert server_kept == sdk_kept, sorted(server_kept ^ sdk_kept)
        seen |= set(chunk)
    assert seen == set(every_attribute()) | {"tool.name"}  # the chunks lose nothing


def test_the_free_text_the_disabled_mode_drops_is_exactly_what_the_sdk_drops(
    sdk: ModuleType,
) -> None:
    """Names the list: `error.message` and `metadata.*`, nothing else (tags, run.name, span.name ...
    stay in every mode, as in the SDK; KI-078)."""
    dropped: set[str] = set()
    for chunk in attribute_chunks():
        raw = wire_event(make_run_ids(), 1, attributes=dict(chunk))
        out = sdk.Redactor(payload_mode=sdk.PayloadMode.DISABLED).apply(
            {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
        )
        dropped |= set(chunk) - set(out["attributes"])
    assert dropped == {"error.message", "metadata.owner", "metadata.ticket.id"}
