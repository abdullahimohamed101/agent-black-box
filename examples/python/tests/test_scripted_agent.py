"""The scripted custom agent, offline: no server, no model key, no network."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blackbox import BlackBox, PayloadMode

import scripted_agent


def play() -> list[dict[str, Any]]:
    bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="demo")
    with bb.run("Answer support ticket", metadata={"model": "scripted-small"}) as run:
        outcome = scripted_agent.run_agent(run, scripted_agent.ScriptedModel())
    assert outcome["retries"] == 1
    return sorted(bb.buffered_events(), key=lambda e: e["sequence"])


def story(events: list[dict[str, Any]]) -> list[str]:
    return [
        e["event_type"]
        for e in events
        if e["event_type"].startswith(("llm.", "tool.", "retry.", "run."))
        and not e["event_type"].endswith(".started")
    ]


def test_the_story_is_plan_fail_retry_succeed_answer() -> None:
    events = story(play())
    assert events == [
        "llm.request.completed",
        "tool.call.failed",
        "retry.attempted",
        "tool.call.completed",
        "llm.request.completed",
        "run.completed",
    ]


def test_two_plays_have_the_same_shape_and_costs() -> None:
    def shape(events: list[dict[str, Any]]) -> list[tuple[str, str | None, Any]]:
        return [
            (e["event_type"], e.get("status"), e["attributes"].get("cost.estimated_usd"))
            for e in events
        ]

    assert shape(play()) == shape(play())


def test_costs_come_from_the_fixed_price_table() -> None:
    costs = [
        e["attributes"]["cost.estimated_usd"]
        for e in play()
        if e["event_type"] == "llm.request.completed"
    ]
    assert costs == [0.001530, 0.002610]


def test_metadata_only_capture_sends_no_payloads() -> None:
    bb = BlackBox(
        mode="offline",
        project="demo",
        api_key="abb_live_k.secret",
        payload_mode=PayloadMode.METADATA_ONLY,
    )
    with bb.run("t") as run:
        scripted_agent.run_agent(run, scripted_agent.ScriptedModel())
    assert all(not e.get("payload") for e in bb.buffered_events())


def test_main_offline_needs_no_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BLACKBOX_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert scripted_agent.main(["--offline"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["retries"] == 1 and summary["run_id"]


def test_live_without_a_key_stops_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        scripted_agent.main(["--live", "--offline"])
