"""When the policy service cannot answer: fail open or closed by risk class, rule and override."""

import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest

from blackbox import BlackBox, PolicyUnavailable
from blackbox.config import Config
from blackbox.policy import WARNING_REASONS
from tests.policy_server import (
    PolicyServer,
    R,
    allow,
    make_client,
    table,
    warnings_of,
)


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


def client(srv: PolicyServer, **options: Any) -> BlackBox:
    options.setdefault("policy_timeout", 0.5)
    return make_client(srv, **options)


def outage_reply() -> list[tuple[str, R, str]]:
    return [
        ("5xx", R(500, {"error": {"code": "X"}}), "policy_error"),
        ("503", R(503), "policy_error"),
        ("drop", R(drop=True), "policy_service_unreachable"),
        ("hold", R(hold=3.0), "policy_timeout"),
        ("429-long", R(429, {"error": {"code": "RATE_LIMITED"}}, {"Retry-After": "30"}), "policy_error"),
        ("403", R(403, {"error": {"code": "FORBIDDEN"}}), "unauthorised"),
        ("401", R(401, {"error": {"code": "UNAUTHENTICATED"}}), "unauthorised"),
        ("422", R(422, {"error": {"code": "INTENT_INVALID"}}), "invalid_intent"),
        ("413", R(413), "intent_too_large"),
        ("404", R(404), "policy_error"),
        ("garbage", R(200, b"<html>"), "policy_error"),
        ("json-list", R(200, b"[1,2]"), "policy_error"),
        ("empty", R(200, b""), "policy_error"),
        ("bad-decision", R(200, {"decision": "maybe", "policy_version": 1}), "policy_error"),
        ("no-version", R(200, {"decision": "allow"}), "policy_error"),
        ("huge", R(200, b"x" * (300 * 1024)), "policy_error"),
        (  # valid JSON padded past the cap: the bound is on size, not on whether it parses
            "padded",
            R(200, b'{"decision":"allow","policy_version":1}' + b" " * (100 * 1024)),
            "policy_error",
        ),
        (
            "approval-without-id",
            R(200, {"decision": "require_approval", "policy_version": 1}),
            "policy_error",
        ),
    ]  # fmt: skip


@pytest.mark.parametrize(("name", "reply", "reason"), outage_reply(), ids=lambda x: str(x)[:20])
@pytest.mark.parametrize(
    ("risk", "closed"),
    [(None, False), ("R0", False), ("R1", False), ("R2", False), ("R3", True), ("R4", True)],
)
def test_outage_follows_the_risk_class_default(
    srv: PolicyServer, name: str, reply: R, reason: str, risk: str | None, closed: bool
) -> None:
    srv.queue("decide", reply, reply)  # twice: a 429 may be retried
    bb = client(srv)
    started = time.monotonic()
    with bb.run("t"):
        if closed:
            with pytest.raises(PolicyUnavailable) as info:
                bb.guard("tool.x", "SECRET-RES", risk, {"a": "SECRET-ARG"})
            assert info.value.reason == reason
            assert "SECRET" not in str(info.value)
        else:
            decision = bb.guard("tool.x", "SECRET-RES", risk, {"a": "SECRET-ARG"})
            assert (decision.decision, decision.via) == ("allow", "fail_open")
    assert time.monotonic() - started < 2.0
    found = warnings_of(srv, bb)
    assert [w["attributes"] for w in found] == [
        {"policy.capability": "tool.x", "policy.reason": reason}
    ]
    assert reason in WARNING_REASONS
    assert "SECRET" not in str(srv.events())
    bb.shutdown()


def test_connection_refused_is_unreachable() -> None:
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    free = port.getsockname()[1]
    port.close()
    bb = BlackBox(
        api_key="k.s", endpoint=f"http://127.0.0.1:{free}", policy_mode="enforce", mode="http",
        policy_timeout=0.5,
    )  # fmt: skip
    with bb.run("t"):
        assert bb.guard("a.b", risk_class="R1").via == "fail_open"
        with pytest.raises(PolicyUnavailable) as info:
            bb.guard("a.b", risk_class="R3")
    assert info.value.reason == "policy_service_unreachable"
    bb.shutdown(1)


@pytest.mark.parametrize("mode", ["offline", "local", "disabled"])
def test_modes_without_a_server_fail_by_mode(mode: str, tmp_path: Any) -> None:
    bb = BlackBox(
        api_key="k.s", policy_mode="enforce", mode=mode, local_path=str(tmp_path / "e.jsonl")
    )
    with bb.run("t"):
        assert bb.guard("a.b", risk_class="R0").via == "fail_open"
        with pytest.raises(PolicyUnavailable):
            bb.guard("a.b", risk_class="R4")
    bb.shutdown(1)


def test_a_429_with_a_short_retry_after_that_keeps_failing_follows_the_mode(
    srv: PolicyServer,
) -> None:
    limited = R(429, {"error": {"code": "RATE_LIMITED"}}, {"Retry-After": "0"})
    srv.queue("decide", limited, limited, limited)
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("a.b", risk_class="R0").via == "fail_open"
    assert len(srv.of("decide")) == 2  # one retry, never a loop
    bb.shutdown()


def test_the_decision_call_is_bounded_even_when_the_server_dribbles(srv: PolicyServer) -> None:
    """A server that sends a byte at a time cannot stretch the call beyond its deadline."""
    import socketserver
    import threading

    class Dribble(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            first = self.request.recv(65536)
            try:
                if b"/v1/policy/decide" not in first:  # events: answer normally
                    self.request.sendall(
                        b"HTTP/1.1 202 Accepted\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
                    )
                    return
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 400\r\n\r\n")
                for _ in range(400):
                    time.sleep(0.1)
                    self.request.sendall(b" ")
            except OSError:
                pass

    slow = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Dribble)
    slow.daemon_threads = True
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    bb = BlackBox(
        api_key="k.s", endpoint=f"http://127.0.0.1:{slow.server_address[1]}",
        policy_mode="enforce", mode="http", policy_timeout=0.4,
    )  # fmt: skip
    started = time.monotonic()
    with bb.run("t"):
        assert bb.guard("a.b", risk_class="R0").via == "fail_open"
    assert time.monotonic() - started < 1.5
    bb.shutdown(0.2)
    slow.shutdown()


# -- overrides -------------------------------------------------------------------------------------


def test_per_capability_override_from_config(srv: PolicyServer) -> None:
    srv.queue("decide", R(drop=True), R(drop=True), R(drop=True))
    bb = client(srv, policy_fail_modes={"shell.execute": "closed", "tool.*": "closed"})
    with bb.run("t"):
        with pytest.raises(PolicyUnavailable):
            bb.guard("shell.execute", "git", "R0")  # closed although R0
        with pytest.raises(PolicyUnavailable):
            bb.guard("tool.anything", "x", "R0")
        assert bb.guard("filesystem.read", "a", "R0").via == "fail_open"
    bb.shutdown()


def test_an_open_override_beats_the_risk_class(srv: PolicyServer) -> None:
    srv.queue("decide", R(drop=True))
    bb = client(srv, policy_fail_modes={"shell.execute": "open"})
    with bb.run("t"):
        assert bb.guard("shell.execute", "rm", "R4").via == "fail_open"
    bb.shutdown()


def test_override_from_the_environment_and_precedence(
    srv: PolicyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLACKBOX_POLICY_FAIL_MODES", "a.b=closed, c.d=open,bad,e.f=sideways")
    cfg = Config.build(policy_fail_modes={"c.d": "closed", "g.h": "bogus"})
    assert cfg.policy_fail_modes == {"a.b": "closed", "c.d": "closed"}
    srv.queue("decide", R(drop=True), R(drop=True))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable):
        bb.guard("a.b", "x", "R0")
    bb.shutdown()


def test_the_exact_override_beats_a_prefix_and_the_longest_prefix_wins() -> None:
    from blackbox.policy_failmodes import override_for

    modes = {"tool.*": "closed", "tool.search.*": "open", "tool.search.web": "closed"}
    assert override_for(modes, "tool.search.web") == "closed"
    assert override_for(modes, "tool.search.x") == "open"
    assert override_for(modes, "tool.other") == "closed"
    assert override_for(modes, "tool") is None  # `prefix.*` needs one more segment
    assert override_for(modes, "other.x") is None


def test_the_on_error_argument_beats_everything(srv: PolicyServer) -> None:
    srv.queue("decide", R(drop=True), R(drop=True))
    bb = client(srv, policy_fail_modes={"a.b": "closed"})
    with bb.run("t"):
        assert bb.guard("a.b", "x", "R4", on_error="open").via == "fail_open"
        with pytest.raises(PolicyUnavailable):
            bb.guard("a.b", "x", "R0", on_error="closed")
    bb.shutdown()


def test_a_bad_on_error_is_ignored_not_trusted(srv: PolicyServer) -> None:
    srv.queue("decide", R(drop=True))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable):
        bb.guard("a.b", "x", "R4", on_error="whatever")
    bb.shutdown()


# -- the cached table ------------------------------------------------------------------------------

RULES = [
    {
        "rule_id": "net", "capability": "shell.execute", "resource": "curl",
        "risk_classes": [], "has_attribute_conditions": False, "on_error": "closed",
    },
    {
        "rule_id": "paths", "capability": "filesystem.*", "resource": "**/.env*",
        "risk_classes": [], "has_attribute_conditions": False, "on_error": "closed",
    },
    {
        "rule_id": "calm", "capability": "github.push", "resource": None,
        "risk_classes": ["R3"], "has_attribute_conditions": False, "on_error": "open",
    },
    {
        "rule_id": "attr-open", "capability": "tool.attr", "resource": None,
        "risk_classes": [], "has_attribute_conditions": True, "on_error": "open",
    },
    {
        "rule_id": "attr-closed", "capability": "tool.strict", "resource": None,
        "risk_classes": [], "has_attribute_conditions": True, "on_error": "closed",
    },
]  # fmt: skip


def test_the_cached_table_is_used_after_an_outage(srv: PolicyServer) -> None:
    srv.default["failmodes"] = table(3, RULES)
    srv.queue("decide", allow())  # the first call succeeds and caches the table
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("a.b").via == "policy"
        assert len(srv.of("failmodes")) == 1
        srv.default["decide"] = R(drop=True)
        with pytest.raises(PolicyUnavailable):  # closed by a rule although unclassified
            bb.guard("shell.execute", "curl", None)
        with pytest.raises(PolicyUnavailable):
            bb.guard("filesystem.read", "config/.env.local", "R0")
        assert bb.guard("filesystem.read", "src/app.py", "R0").via == "fail_open"
        assert bb.guard("github.push", "origin main", "R3").via == "fail_open"  # rule: open
        assert bb.guard("github.push", "origin main", None).via == "fail_open"
        with pytest.raises(PolicyUnavailable):
            bb.guard("shell.execute", "rm", "R3")  # no rule: the risk class default
        with pytest.raises(PolicyUnavailable):
            bb.guard("tool.attr", "", "R3")  # attribute rule that says open is not believed
        with pytest.raises(PolicyUnavailable):
            bb.guard("tool.strict", "", "R0")  # one that says closed is
    bb.shutdown()


def test_without_a_cached_table_only_the_defaults_apply(srv: PolicyServer) -> None:
    srv.default["decide"] = R(drop=True)  # down from the start: the table was never cached
    srv.default["failmodes"] = table(3, RULES)
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("shell.execute", "curl", "R0").via == "fail_open"
    bb.shutdown()


def test_the_table_is_refreshed_when_the_policy_version_moves(srv: PolicyServer) -> None:
    srv.default["failmodes"] = table(3, [])
    srv.default["decide"] = allow()
    bb = client(srv)
    with bb.run("t"):
        bb.guard("a.b")
        bb.guard("a.b")
        assert len(srv.of("failmodes")) == 1  # no refetch within five minutes
        srv.default["failmodes"] = table(4, RULES)
        srv.default["decide"] = allow(policy_version=4)
        time.sleep(0.01)
        bb.guard("a.b")  # the decision shows version 4 > the cached 3
    assert len(srv.of("failmodes")) == 2
    engine = bb._policy_engine()
    assert engine._table is not None and engine._table.policy_version == 4
    bb.shutdown()


def test_the_table_is_refetched_after_five_minutes(
    srv: PolicyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    bb = client(srv)
    engine = bb._policy_engine()
    engine._clock = lambda: clock[0]
    with bb.run("t"):
        bb.guard("a.b")
        clock[0] += 299
        bb.guard("a.b")
        assert len(srv.of("failmodes")) == 1
        clock[0] += 2
        bb.guard("a.b")
    assert len(srv.of("failmodes")) == 2
    bb.shutdown()


def test_a_failing_table_fetch_never_breaks_or_slows_a_decision(srv: PolicyServer) -> None:
    srv.default["failmodes"] = R(hold=3.0)
    bb = client(srv, policy_timeout=2.0)
    started = time.monotonic()
    with bb.run("t"):
        assert bb.guard("a.b").via == "policy"
        assert time.monotonic() - started < 1.8  # bounded by the short table timeout (1 s)
        bb.guard("a.b")
        assert len(srv.of("failmodes")) == 1  # a failed fetch waits a minute before the next
    bb.shutdown()


@pytest.mark.parametrize(
    "junk",
    [R(500), R(200, b"nope"), R(200, {"policy_version": "x", "rules": []}),
     R(200, {"policy_version": 1, "rules": "no"}),
     R(200, {"policy_version": 1, "rules": [1, None, {"on_error": "open"}, {"on_error": "maybe"}]})],
)  # fmt: skip
def test_a_malformed_table_is_ignored(srv: PolicyServer, junk: R) -> None:
    srv.default["failmodes"] = junk
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("a.b").via == "policy"
        srv.default["decide"] = R(drop=True)
        assert bb.guard("a.b", risk_class="R1").via == "fail_open"
        with pytest.raises(PolicyUnavailable):
            bb.guard("a.b", risk_class="R3")
    bb.shutdown()


def test_the_servers_defaults_in_the_table_are_used_for_unlisted_actions(
    srv: PolicyServer,
) -> None:
    srv.default["failmodes"] = table(3, [], unclassified="closed", R2="closed")
    bb = client(srv)
    with bb.run("t"):
        bb.guard("a.b")
        srv.default["decide"] = R(drop=True)
        with pytest.raises(PolicyUnavailable):
            bb.guard("a.b", "x", None)
        with pytest.raises(PolicyUnavailable):
            bb.guard("a.b", "x", "R2")
        assert bb.guard("a.b", "x", "R1").via == "fail_open"
    bb.shutdown()


# -- inputs that never reach the server ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"capability": "has space"}, "invalid_intent"),
        ({"capability": "a.b", "arguments": {"x": object()}}, "invalid_intent"),
        ({"capability": "a.b", "arguments": {"x": "y" * 5000}}, "intent_too_large"),
        ({"capability": "a.b", "resource": "r" * 3000}, "intent_too_large"),
        ({"capability": "a.b", "risk_class": "R7"}, "invalid_intent"),
    ],
)
def test_an_unsendable_intent_follows_the_fail_mode_without_a_request(
    srv: PolicyServer, kwargs: dict[str, Any], reason: str
) -> None:
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard(**kwargs).via == "fail_open"
        with pytest.raises(PolicyUnavailable) as info:
            bb.guard(**{**kwargs, "on_error": "closed"})
    assert info.value.reason == reason and srv.of("decide") == []
    assert {w["attributes"]["policy.reason"] for w in warnings_of(srv, bb)} == {reason}
    bb.shutdown()


def test_no_run_means_no_question_and_the_fail_mode() -> None:
    bb = BlackBox(api_key="k.s", mode="offline", policy_mode="enforce")
    assert bb.guard("a.b", risk_class="R0").via == "fail_open"
    with pytest.raises(PolicyUnavailable) as info:
        bb.guard("a.b", risk_class="R3")
    assert info.value.reason == "invalid_intent"
    bb.shutdown()
