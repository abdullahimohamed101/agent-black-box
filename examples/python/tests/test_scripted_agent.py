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


def play_variant(variant: str, ticket: str = "T-1043") -> tuple[list[dict[str, Any]], str | None]:
    bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="demo")
    error: str | None = None
    try:
        with bb.run("Answer support ticket", metadata={"model": "scripted-small"}) as run:
            scripted_agent.run_agent(
                run,
                scripted_agent.ScriptedModel(scripted_agent.TICKETS[ticket]),
                ticket=scripted_agent.TICKETS[ticket],
                variant=variant,
            )
    except (RuntimeError, scripted_agent.PermissionDenied) as exc:
        error = type(exc).__name__
    return sorted(bb.buffered_events(), key=lambda e: e["sequence"]), error


def test_outage_fails_the_run_after_two_retries_with_a_named_error() -> None:
    events, error = play_variant("outage")
    assert error == "RuntimeError"
    kinds = [e["event_type"] for e in events]
    assert kinds.count("retry.attempted") == 2 and kinds[-1] == "run.failed"
    failed = [e for e in events if e["event_type"] == "tool.call.failed"]
    assert len(failed) == 3
    assert {e["attributes"]["error.type"] for e in failed} == {"ConnectionTimeout"}
    assert kinds.count("timeout.occurred") == 3
    # one model call after the first error: the escalation note
    assert kinds.count("llm.request.completed") == 2


def test_denied_fails_at_once_without_a_retry() -> None:
    events, error = play_variant("denied")
    assert error == "PermissionDenied"
    kinds = [e["event_type"] for e in events]
    assert "retry.attempted" not in kinds and kinds[-1] == "run.failed"


def test_clean_has_no_failure_and_flaky_retries_five_times() -> None:
    clean, _ = play_variant("clean")
    assert "tool.call.failed" not in [e["event_type"] for e in clean]
    flaky, error = play_variant("flaky")
    assert error is None
    assert [e["event_type"] for e in flaky].count("retry.attempted") == 5


def test_main_failing_variant_exits_one_and_still_prints_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert scripted_agent.main(["--offline", "--variant", "outage", "--ticket", "T-1047"]) == 1
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["failed"] is True and summary["retries"] == 0 and summary["run_id"]
