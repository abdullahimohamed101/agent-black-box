"""INV-4 for guard(): only the policy exceptions escape, nothing blocks, nothing is logged."""

import ast
import logging
import random
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import blackbox
from blackbox import (
    BlackBox,
    Decision,
    PolicyDenied,
    PolicyError,
    PolicyUnavailable,
    policy_transport,
)
from blackbox import policy as policy_module
from blackbox.policy import WARNING_REASONS, PolicyGuard
from tests.policy_server import PolicyServer, deny, make_client, require, state, warnings_of

SRC = Path(blackbox.__file__).parent
SENTINEL = "SENTINEL-sk-live-0123456789abcdef"


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "WAIT_MARGIN", 0.15)
    monkeypatch.setattr(PolicyGuard, "_backoff", lambda self, failures, retry_after: 0.0)


def client(srv: PolicyServer, **options: Any) -> BlackBox:
    options.setdefault("policy_timeout", 0.5)
    options.setdefault("policy_wait_round", 0.05)
    return make_client(srv, **options)


# -- standard library only -------------------------------------------------------------------------


def test_policy_modules_import_only_the_standard_library() -> None:
    files = sorted(SRC.glob("policy*.py"))
    assert {f.name for f in files} >= {
        "policy.py", "policy_intent.py", "policy_transport.py",
        "policy_failmodes.py", "policy_glob.py",
    }  # fmt: skip
    for file in files:
        for node in ast.walk(ast.parse(file.read_text("utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top == "blackbox" or top in sys.stdlib_module_names, (file.name, name)


def test_the_package_still_declares_no_runtime_dependency() -> None:
    pyproject = (SRC.parents[1] / "pyproject.toml").read_text("utf-8")
    assert "dependencies = []" in pyproject


# -- nothing is logged -----------------------------------------------------------------------------


def test_arguments_and_resources_are_never_logged_or_recorded(
    srv: PolicyServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bb = client(srv)
    secret_args = {"token": SENTINEL, "nested": {"cmd": f"curl -H 'Authorization: {SENTINEL}'"}}
    messages: list[str] = []
    with bb.run("t"):
        bb.guard("a.b", SENTINEL, "R0", secret_args)  # allow
        srv.queue("decide", deny())
        try:
            bb.guard("a.b", SENTINEL, "R0", secret_args)
        except PolicyDenied as exc:
            messages.append(str(exc))
        srv.queue("decide", require(), require())
        srv.queue("wait", state("DENIED"), state("APPROVED"))
        srv.queue("consume", state("NOPE"))
        for _ in range(2):
            try:
                bb.guard("a.b", SENTINEL, "R0", secret_args)
            except PolicyError as exc:
                messages.append(str(exc))
        srv.default["decide"] = state("junk")  # malformed: fail open, warning
        bb.guard("a.b", SENTINEL, "R0", secret_args)
        try:
            bb.guard("a.b", SENTINEL, "R4", secret_args)
        except PolicyError as exc:
            messages.append(str(exc))
        try:
            bb.guard("a b", SENTINEL, "R4", secret_args, on_error="closed")  # invalid capability
        except PolicyError as exc:
            messages.append(str(exc))
        bb.guard("a.b", SENTINEL, "R0", {"x": object()})  # unsendable
    bb.flush(3)
    assert SENTINEL not in caplog.text
    assert not any(SENTINEL in m for m in messages)
    assert SENTINEL not in str(srv.events())
    for event in srv.events():
        if event["event_type"] == "policy.warning":
            assert set(event["attributes"]) == {"policy.capability", "policy.reason"}
            assert event["attributes"]["policy.reason"] in WARNING_REASONS
    bb.shutdown()


# -- INV-4 fuzz ------------------------------------------------------------------------------------

ERRORS: list[type[BaseException]] = [
    ValueError, RuntimeError, KeyError, TypeError, AttributeError, ZeroDivisionError, OSError,
    UnicodeError, AssertionError, RecursionError, MemoryError, TimeoutError, IndexError,
    NotImplementedError, OverflowError, LookupError, StopIteration, ConnectionResetError,
]  # fmt: skip

TARGETS = [
    (policy_module, "build_intent"),
    (policy_module, "_parse_verdict"),
    (policy_module, "parse_timestamp"),
    (policy_module, "override_for"),
    (policy_module, "default_mode"),
    (policy_module.PolicyGuard, "_decide"),
    (policy_module.PolicyGuard, "_call"),
    (policy_module.PolicyGuard, "_await_approval"),
    (policy_module.PolicyGuard, "_settle"),
    (policy_module.PolicyGuard, "_consume"),
    (policy_module.PolicyGuard, "_read_wait"),
    (policy_module.PolicyGuard, "_refresh_table"),
    (policy_module.PolicyGuard, "_mode"),
    (policy_module.PolicyGuard, "_warn"),
    (policy_module.PolicyGuard, "_pause"),
    (policy_module.PolicyGuard, "_cancel_quietly"),
    (policy_transport.PolicyTransport, "request"),
]


@pytest.mark.parametrize("scenario", ["allow", "deny", "approve", "outage"])
def test_fuzz_only_the_documented_exceptions_escape(srv: PolicyServer, scenario: str) -> None:
    rng = random.Random(scenario)
    monkeypatch = pytest.MonkeyPatch()
    bb = client(srv, policy_timeout=0.3)
    documented = (PolicyError,)
    outcomes = {"decision": 0, "denied": 0, "unavailable": 0}
    try:
        with bb.run("t"):
            for round_number in range(120):
                owner, name = rng.choice(TARGETS)
                error = rng.choice(ERRORS)
                risk = rng.choice([None, "R0", "R2", "R3", "R4"])
                real = getattr(owner, name)
                fire_on = rng.randint(1, 3)
                counter = [0]

                def broken(
                    *args: Any,
                    _real: Any = real,
                    _error: Any = error,
                    _n: list[int] = counter,
                    _fire: int = fire_on,
                    **kwargs: Any,
                ) -> Any:
                    _n[0] += 1
                    if _n[0] == _fire:
                        raise _error("injected")
                    return _real(*args, **kwargs)  # fmt: skip

                monkeypatch.setattr(
                    owner, name, staticmethod(broken) if name == "_read_wait" else broken
                )
                if scenario == "deny":
                    srv.queue("decide", deny())
                elif scenario == "approve":
                    srv.queue("decide", require(approval_id=f"apr_{round_number}"))
                    srv.queue("wait", state("REQUESTED", f"apr_{round_number}"),
                              state("APPROVED", f"apr_{round_number}"))  # fmt: skip
                else:
                    srv.queue("decide", state("junk"))
                started = time.monotonic()
                try:
                    result = bb.guard("shell.execute", "git", risk, {"n": round_number})
                    assert isinstance(result, Decision) and result.decision == "allow"
                    outcomes["decision"] += 1
                except documented as exc:
                    outcomes["denied" if isinstance(exc, PolicyDenied) else "unavailable"] += 1
                finally:
                    monkeypatch.undo()
                assert time.monotonic() - started < 4, (owner, name, error)
                srv.script["wait"].clear()
                srv.script["decide"].clear()
                srv.script["consume"].clear()
    finally:
        monkeypatch.undo()
        bb.shutdown()
    assert sum(outcomes.values()) == 120  # every call ended in a Decision or a policy exception


def test_a_malformed_capability_never_reaches_the_warning_or_the_exception(
    srv: PolicyServer,
) -> None:
    bb = client(srv)
    hostile = f"cap {SENTINEL} with spaces"
    with bb.run("t"):
        assert bb.guard(hostile, "r", "R0").via == "fail_open"
        with pytest.raises(PolicyUnavailable) as info:
            bb.guard(hostile, "r", "R3")
    assert SENTINEL not in str(info.value) and info.value.capability == ""
    found = warnings_of(srv, bb)
    assert [w["attributes"] for w in found] == [
        {"policy.capability": "invalid", "policy.reason": "invalid_intent"}
    ] * 2
    assert SENTINEL not in str(srv.events()) and srv.of("decide") == []
    bb.shutdown()


def test_a_bug_in_the_fail_path_itself_is_still_contained(
    srv: PolicyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    bb = client(srv)
    monkeypatch.setattr(PolicyGuard, "_guard", boom)
    monkeypatch.setattr(PolicyGuard, "_mode", boom)
    monkeypatch.setattr(PolicyGuard, "_warn", boom)
    with bb.run("t"):
        assert bb.guard("a.b", "x", "R1").via == "fail_open"
        with pytest.raises(PolicyUnavailable) as info:
            bb.guard("a.b", "x", "R3")
    assert info.value.reason == "internal_error"
    bb.shutdown()


def test_an_internal_bug_after_the_approval_exists_withdraws_it(
    srv: PolicyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv.queue("decide", require())

    def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(PolicyGuard, "_settle", boom)
    srv.queue("wait", state("APPROVED"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable):
        bb.guard("github.push", "origin main", "R3")
    assert len(srv.of("cancel")) == 1  # the waiting request does not linger
    bb.shutdown()


def test_hostile_arguments_cannot_break_guard(srv: PolicyServer) -> None:
    class Hostile:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("no")

        def __repr__(self) -> str:
            raise RuntimeError("no")

    cyclic: list[Any] = []
    cyclic.append(cyclic)
    bb = client(srv)
    with bb.run("t"):
        for args in (
            {"a": Hostile()}, {"a": cyclic}, {"a": {1, 2}}, {"a": lambda: 1}, {"a": float("inf")},
            {"a" * 300: 1}, {"a": "\ud800"}, {"a": "x\x00y"}, ["not", "a", "dict"], 5, "text",
        ):  # fmt: skip
            result = bb.guard("a.b", "r", "R0", args)  # type: ignore[arg-type]
            assert isinstance(result, Decision)
        for resource in (5, b"bytes", object(), ["x"]):
            assert isinstance(bb.guard("a.b", resource, "R0"), Decision)  # type: ignore[arg-type]
        for cap in (None, 5, "", "x" * 500, "bad cap", b"b"):
            assert isinstance(bb.guard(cap, "r", "R0"), Decision)  # type: ignore[arg-type]
    bb.shutdown()


def test_guard_does_not_touch_its_arguments(srv: PolicyServer) -> None:
    arguments: dict[str, Any] = {"t": 0.5, "nested": {"x": (1, 2)}}
    before = repr(arguments)
    bb = client(srv)
    with bb.run("t"):
        bb.guard("a.b", "r", "R0", arguments)
    assert repr(arguments) == before  # floats were converted in a copy
    bb.shutdown()


def test_a_forked_client_gets_its_own_engine(srv: PolicyServer) -> None:
    bb = client(srv)
    first = bb._policy_engine()
    bb._after_fork()
    assert bb._policy_engine() is not first
    bb.shutdown()


def test_a_run_from_another_thread_is_not_required(srv: PolicyServer) -> None:
    bb = client(srv)
    with bb.run("t") as run:
        import threading

        out: list[Any] = []

        def worker() -> None:
            out.append(bb.guard("a.b", run=run))

        t = threading.Thread(target=worker)
        t.start()
        t.join(5)
    assert out and out[0].via == "policy"  # run= carries the context across threads
    bb.shutdown()


def test_the_timestamp_parser_reads_what_the_server_writes() -> None:
    from blackbox.policy import parse_timestamp

    assert parse_timestamp("2026-10-13T12:00:00Z") == 1791892800.0
    assert parse_timestamp("2026-10-13T12:00:00.5Z") == 1791892800.5
    assert parse_timestamp("2026-10-13T12:00:00.123456789Z") == pytest.approx(1791892800.123456)
    assert parse_timestamp("2026-10-13T14:00:00+02:00") == 1791892800.0
    assert parse_timestamp("2026-10-13T12:00:00") == 1791892800.0
    for bad in ("", "tomorrow", "2026-13-45T00:00:00Z", None, 5, "2026-10-13"):
        assert parse_timestamp(bad) is None
