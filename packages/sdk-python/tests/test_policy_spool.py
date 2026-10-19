"""KI-152: a `policy.warning` survives an outage longer than the exporter's retry window."""

import time
from collections.abc import Iterator
from typing import Any

import pytest

from agent_blackbox.policy_spool import REASON_DROPPED, WarningSpool
from agent_blackbox.stats import Stats
from tests.policy_server import PolicyServer, R, make_client


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


def client(srv: PolicyServer) -> Any:
    return make_client(srv, policy_timeout=0.5, max_attempts=2, backoff_base=0.0, backoff_max=0.0)


def warnings(srv: PolicyServer) -> list[dict[str, Any]]:
    return [e for e in srv.events() if e["event_type"] == "policy.warning"]


def settle(srv: PolicyServer, bb: Any, expect: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        bb.flush(1)
        found = warnings(srv)
        if len(found) >= expect:
            return found
        time.sleep(0.05)
    return warnings(srv)


def test_a_warning_dropped_during_an_outage_arrives_with_the_next_successful_decision(
    srv: PolicyServer,
) -> None:
    srv.events_down = True  # the API is down for longer than the exporter retries
    srv.queue("decide", R(500, {"error": {"code": "X"}}))
    bb = client(srv)
    with bb.run("t") as run:
        assert bb.guard("filesystem.read", "x", "R1", run=run).via == "fail_open"
        deadline = time.monotonic() + 3
        while bb.stats()["dropped_export_failed"] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bb.stats()["dropped_export_failed"] > 0 and warnings(srv) == []
        srv.events_down = False  # the API is back
        assert bb.guard("filesystem.read", "y", "R1", run=run).via == "policy"
        found = settle(srv, bb, 1)
    assert [w["attributes"]["policy.reason"] for w in found] == ["policy_error"]
    bb.shutdown()


def test_a_warning_is_re_sent_at_flush_and_never_twice(srv: PolicyServer) -> None:
    srv.events_down = True
    srv.queue("decide", R(500, {"error": {"code": "X"}}))
    bb = client(srv)
    with bb.run("t") as run:
        bb.guard("filesystem.read", "x", "R1", run=run)
        deadline = time.monotonic() + 3
        while bb.stats()["dropped_export_failed"] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        srv.events_down = False
        found = settle(srv, bb, 1)
        assert len(found) == 1
        bb.flush(1)
        bb.flush(1)
    assert len(warnings(srv)) == 1
    bb.shutdown()


def test_the_spool_is_bounded_and_reports_what_it_lost_once() -> None:
    stats = Stats()
    spool = WarningSpool(stats, cap=3)
    template = {
        "event_type": "policy.warning",
        "event_id": "evt_x",
        "run_id": "run_x",
        "sequence": 7,
        "attributes": {"policy.capability": "a.b", "policy.reason": "policy_error"},
    }
    spool.offer([{**template, "event_id": f"evt_{i}"} for i in range(8)])
    spool.offer([{"event_type": "tool.call.completed"}])  # only warnings are kept
    taken = spool.take_all()
    assert len(taken) == 4 and [e["event_id"] for e in taken[:3]] == ["evt_5", "evt_6", "evt_7"]
    report = taken[-1]
    assert report["attributes"]["policy.reason"] == REASON_DROPPED
    assert report["attributes"]["policy.warnings_dropped"] == 5 and "sequence" not in report
    assert report["event_id"] not in {e["event_id"] for e in taken[:3]}
    assert stats["policy_warnings_lost"] == 5
    assert spool.take_all() == []  # reported once
