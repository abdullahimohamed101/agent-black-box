"""A NUL byte must never hide a secret from redaction (INV-5).

The contract rejects NUL, so the SDK strips it. The strip once ran AFTER the redactor, which let
`AKIA\\x00IOSFODNN7EXAMPLE` through and reassembled it. These tests look at the bytes that would
be exported, not at the redactor alone.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from agent_blackbox import BlackBox
from agent_blackbox.coding import CodingRecorder
from agent_blackbox.redaction import PayloadMode
from tests.helpers import events_of, offline

AWS = "AKIAIOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "A" * 36
SLACK = "xoxb-1234567890-abcdefghijkl"
SECRETS = [AWS, GITHUB, SLACK, "hunter2"]


def split(secret: str) -> str:
    return secret[:3] + "\x00" + secret[3:]


def assert_clean(bb: BlackBox) -> list[dict[str, Any]]:
    """Check the exported bytes; returns the events (reading the buffer drains it)."""
    events = events_of(bb)
    dump = json.dumps(events)
    assert "\\u0000" not in dump
    for secret in SECRETS:
        assert secret not in dump, secret
    return events


@pytest.mark.parametrize("secret", [AWS, GITHUB, SLACK])
def test_a_nul_split_secret_in_a_payload_is_redacted(secret: str) -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        run.event("custom.nul", payload={"text": f"x {split(secret)} y"})
    events = assert_clean(bb)
    (event,) = [e for e in events if e["event_type"] == "custom.nul"]
    assert "[REDACTED:" in event["payload"]["text"]


def test_a_nul_split_denied_key_is_redacted() -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        run.event("custom.nul", payload={"pass\x00word": "hunter2", "n": {"to\x00ken": "hunter2"}})
    events = assert_clean(bb)
    (event,) = [e for e in events if e["event_type"] == "custom.nul"]
    assert event["payload"]["password"] == "[REDACTED:password]"
    assert event["payload"]["n"]["token"] == "[REDACTED:token]"


def test_nul_split_secrets_in_nested_payload_structures_are_redacted() -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    nested: dict[str, Any] = {
        "a": [{"b": [split(AWS), {split("k"): split(GITHUB)}]}],
        "c": (split(SLACK),),
    }
    with bb.run("r") as run:
        run.event("custom.nul", payload=nested)
    assert_clean(bb)


def test_nul_split_secrets_in_attributes_and_tags_are_redacted() -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    with bb.run("r", tags=(split(AWS), split(GITHUB))) as run:
        run.event(
            "custom.nul",
            attributes={"metadata.a": split(SLACK), "metadata.b": [split(AWS)]},
        )
    events = assert_clean(bb)
    started = next(e for e in events if e["event_type"] == "run.started")
    assert any("[REDACTED:" in t for t in started["tags"])


def test_agent_spawn_input_and_result_with_nul_split_secrets_are_redacted() -> None:
    bb = offline(payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        child = run.agent("worker", input={"p": split(AWS), "pass\x00word": "hunter2"})
        child.set_result({"r": split(SLACK), "g": split(GITHUB)})
        child.end()
    assert_clean(bb)


def test_the_coding_recorders_sanitize_path_sees_the_joined_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLANTED_API_SECRET", "ZZ-planted-secret-value-123")
    bb = BlackBox(
        api_key="abb_live_k.secret", mode="offline", project="demo", payload_mode=PayloadMode.FULL
    )
    run = bb.run("coding")
    rec = CodingRecorder(bb, run, tmp_path)
    for secret in (AWS, GITHUB, SLACK, "ZZ-planted-secret-value-123"):
        out = rec.sanitize(f"a {split(secret)} b")
        assert secret not in out and "\x00" not in out, secret
    assert "ZZ-planted" not in rec.sanitize("ZZ-pl\x00anted-secret-value-123")
