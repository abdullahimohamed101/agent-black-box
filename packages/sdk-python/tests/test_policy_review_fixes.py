"""Phase 14 review fixes in the SDK: P2-1 (hostile verdicts), P2-5 (cancelled), P3-7 (skew)."""

import time
from collections.abc import Iterator
from typing import Any

import pytest

from blackbox import (
    ApprovalDenied,
    ApprovalExpired,
    PolicyDenied,
    PolicyUnavailable,
)
from blackbox import policy as policy_module
from blackbox.policy import PolicyGuard
from tests.policy_server import PolicyServer, R, make_client, require, state, warnings_of


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "WAIT_MARGIN", 0.15)
    monkeypatch.setattr(policy_module, "ROUND_SLACK", 1.0)
    monkeypatch.setattr(PolicyGuard, "_backoff", lambda self, failures, retry_after: 0.0)


def client(srv: PolicyServer, **options: Any) -> Any:
    options.setdefault("policy_timeout", 1.0)
    options.setdefault("policy_wait_round", 0.05)
    return make_client(srv, **options)


# -- P2-1: the decision is read first --------------------------------------------------------

HOSTILE_DENIES: list[dict[str, Any]] = [
    {"decision": "deny", "policy_version": "3", "policy_id": "r", "reason": "x"},
    {"decision": "deny"},
    {"decision": "deny", "policy_version": None, "policy_id": 7, "reason": None},
    {"decision": "deny", "policy_version": 10**40, "policy_id": "r", "reason": "x" * 5000},
    {"decision": "deny", "policy_version": -1, "policy_id": ["a"], "reason": {"a": 1}},
    {"decision": "deny", "policy_version": 3.5, "policy_id": "r", "extra": {"deep": [1] * 50}},
    {"decision": "deny", "policy_version": True, "policy_id": None},
]


@pytest.mark.parametrize("body", HOSTILE_DENIES)
@pytest.mark.parametrize("risk", [None, "R0", "R2"])
def test_a_malformed_deny_still_denies_at_every_risk_class(
    srv: PolicyServer, body: dict[str, Any], risk: str | None
) -> None:
    srv.queue("decide", R(200, body))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyDenied):
        bb.guard("filesystem.read", "x", risk)
    bb.shutdown()


HOSTILE_UNKNOWN: list[Any] = ["DENY", "block", "", None, 5, ["deny"], {"deny": 1}, True]


@pytest.mark.parametrize("value", HOSTILE_UNKNOWN)
@pytest.mark.parametrize("risk", [None, "R0", "R2"])
def test_an_unknown_decision_fails_closed_at_every_risk_class(
    srv: PolicyServer, value: Any, risk: str | None
) -> None:
    srv.queue("decide", R(200, {"decision": value, "policy_version": 3}))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable) as info:
        bb.guard("filesystem.read", "x", risk)
    assert info.value.reason == policy_module.REASON_ERROR
    bb.shutdown()


BAD_APPROVALS: list[dict[str, Any]] = [
    {"approval_id": None},
    {"approval_id": 7},
    {"approval_id": "../x"},
    {"expires_at": None},
    {"expires_at": "tomorrow"},
    {"approval_id": None, "expires_at": None},
]


@pytest.mark.parametrize("override", BAD_APPROVALS)
@pytest.mark.parametrize("risk", [None, "R0", "R2"])
def test_a_require_approval_without_a_usable_approval_fails_closed(
    srv: PolicyServer, override: dict[str, Any], risk: str | None
) -> None:
    base = require().body
    srv.queue("decide", R(200, {**base, **override}))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable):
        bb.guard("github.push", "main", risk)
    assert srv.of("wait") == [] and srv.of("consume") == []
    bb.shutdown()


def test_a_server_error_still_follows_the_risk_class(srv: PolicyServer) -> None:
    srv.queue("decide", R(500, {"error": {"code": "X"}}))
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("filesystem.read", "x", "R1").via == "fail_open"
    bb.shutdown()


# -- P2-5: a cancelled approval is never an expiry --------------------------------------------


def test_a_cancelled_approval_denies_even_with_on_expiry_allow(
    srv: PolicyServer, fast: None
) -> None:
    srv.queue("decide", require(on_expiry="allow"))
    srv.queue("wait", state("CANCELLED", on_expiry="allow"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyDenied) as info:
        bb.guard("github.push", "origin main")
    assert isinstance(info.value, ApprovalExpired)
    assert srv.of("consume") == [] and warnings_of(srv, bb) == []
    bb.shutdown()


def test_an_expired_approval_still_follows_on_expiry(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require(on_expiry="allow"))
    srv.queue("wait", state("EXPIRED", on_expiry="allow"))
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("github.push", "origin main").via == "expiry_allow"
    bb.shutdown()


# -- P3-7: a skewed server clock cannot cancel a live approval --------------------------------


def test_a_server_date_far_ahead_does_not_shorten_the_wait(srv: PolicyServer, fast: None) -> None:
    ahead = time.time() + 3 * 3600
    srv.queue("decide", R(200, require(expires_in=3600).body, headers={"Date": _http_date(ahead)}))
    srv.queue("wait", state("REQUESTED"))
    srv.queue("wait", state("DENIED"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(ApprovalDenied):
        bb.guard("github.push", "origin main")
    assert srv.of("cancel") == []  # the approval was not withdrawn for a skew
    bb.shutdown()


def _http_date(when: float) -> str:
    from email.utils import formatdate

    return formatdate(when, usegmt=True)
