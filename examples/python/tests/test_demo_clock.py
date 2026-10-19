"""The virtual clock of `make demo`: timestamps in the past, durations that agree with them, nothing waits."""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_blackbox.client
import agent_blackbox.events
from agent_blackbox import BlackBox

import scripted_agent
from demo_clock import DemoClock, hours


@pytest.fixture(autouse=True)
def restore_clocks() -> Any:
    now, shim = agent_blackbox.events.now_rfc3339, agent_blackbox.client.time
    yield
    agent_blackbox.events.now_rfc3339, agent_blackbox.client.time = now, shim


def play() -> list[dict[str, Any]]:
    clock = DemoClock(hours(30))
    bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="demo")
    model = scripted_agent.LatentModel(
        scripted_agent.ScriptedModel(tokens_scale=16), clock, lambda reply: reply.output_tokens
    )
    with bb.run("Answer support ticket", metadata={"model": model.name}) as run:
        scripted_agent.run_agent(run, model, clock=clock)
    return sorted(bb.buffered_events(), key=lambda e: e["sequence"])


def test_a_run_is_stamped_in_the_past_and_lasts_like_work_without_waiting() -> None:
    started = time.monotonic()
    events = play()
    assert time.monotonic() - started < 5, "the virtual clock must not sleep"
    first, last = events[0], events[-1]
    begin = datetime.fromisoformat(first["occurred_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(last["occurred_at"].replace("Z", "+00:00"))
    hours_ago = (datetime.now(begin.tzinfo) - begin).total_seconds() / 3600
    assert 29.9 < hours_ago < 30.1
    assert 20 < (end - begin).total_seconds() < 90
    # the run's own duration agrees with the timestamps it carries
    assert last["duration_ms"] / 1000 == pytest.approx((end - begin).total_seconds(), abs=1.0)


def test_scaled_tokens_give_real_sized_costs() -> None:
    costs = [
        e["attributes"]["cost.estimated_usd"]
        for e in play()
        if e["event_type"] == "llm.request.completed"
    ]
    assert 0.03 < sum(costs) < 0.2
