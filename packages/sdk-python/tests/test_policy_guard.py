"""guard(): decisions, approvals, consumption, bounded waits (ADR-141, Phase 14 gate 7)."""

import asyncio
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

import blackbox
from blackbox import (
    ApprovalDenied,
    ApprovalExpired,
    BlackBox,
    Decision,
    PolicyDenied,
    PolicyError,
    PolicyUnavailable,
    policy_transport,
)
from blackbox import policy as policy_module
from blackbox.policy import PolicyGuard
from tests.policy_server import (
    PolicyServer,
    R,
    allow,
    consumed,
    deny,
    make_client,
    refused,
    require,
    state,
    warnings_of,
)


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short rounds, margin and backoff: waits are real but milliseconds long."""
    monkeypatch.setattr(policy_module, "WAIT_MARGIN", 0.15)
    monkeypatch.setattr(policy_module, "ROUND_SLACK", 1.0)
    monkeypatch.setattr(PolicyGuard, "_backoff", lambda self, failures, retry_after: 0.0)


def client(srv: PolicyServer, **options: Any) -> BlackBox:
    options.setdefault("policy_timeout", 1.0)
    options.setdefault("policy_wait_round", 0.05)
    return make_client(srv, **options)


# -- off by default --------------------------------------------------------------------------------


def test_policy_is_off_by_default_and_makes_no_network_call(srv: PolicyServer) -> None:
    bb = BlackBox(api_key="abb_live_test.secret", endpoint=srv.url, mode="http")
    with bb.run("t"):
        decision = bb.guard("shell.execute", "git", "R4", {"a": 1})
    assert decision.decision == "allow" and decision.via == "disabled"
    assert srv.of("decide") == [] and srv.of("failmodes") == []
    bb.shutdown()


def test_env_turns_enforcement_on(srv: PolicyServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKBOX_POLICY", "enforce")
    bb = BlackBox(api_key="abb_live_test.secret", endpoint=srv.url, mode="http")
    assert bb.config.policy_mode == "enforce"
    monkeypatch.setenv("BLACKBOX_POLICY", "nonsense")
    assert BlackBox(api_key="k", mode="offline").config.policy_mode == "off"
    assert BlackBox(api_key="k", mode="offline", policy_mode="enforce").config.policy_mode == (
        "enforce"
    )
    bb.shutdown()


def test_module_level_guard_without_a_run_or_client_is_disabled() -> None:
    assert blackbox.guard("shell.execute").via == "disabled"


def test_module_level_guard_uses_the_current_run(srv: PolicyServer) -> None:
    bb = client(srv)
    with bb.run("t"):
        assert blackbox.guard("shell.execute", "git").via == "policy"
    assert len(srv.of("decide")) == 1
    bb.shutdown()


# -- allow and deny --------------------------------------------------------------------------------


def test_allow_returns_the_decision_and_sends_the_whole_intent(srv: PolicyServer) -> None:
    srv.queue("decide", allow(policy_id="r1", reason="fine"))
    bb = client(srv)
    with bb.run("t") as run:
        decision = bb.guard("shell.execute", "git", "R2", {"category": "NETWORK", "n": 2, "t": 0.5})
    assert decision == Decision("allow", "r1", 3, "fine", None, "policy")
    request = srv.of("decide")[0]
    assert request.headers["authorization"] == "Bearer abb_live_test.secret"
    body = request.body
    assert body is not None
    assert body["run_id"] == run.run_id and body["agent_id"] == "demo"
    assert (body["capability"], body["resource"], body["risk_class"]) == (
        "shell.execute", "git", "R2",
    )  # fmt: skip
    assert body["arguments"] == {"category": "NETWORK", "n": 2, "t": "0.5"}  # floats as strings
    assert re.fullmatch(r"g[0-9a-f]{30}", body["idempotency_key"])
    assert body["trace_id"] == run.trace_id
    bb.shutdown()


def test_deny_raises_with_the_policy_fields(srv: PolicyServer) -> None:
    srv.queue("decide", deny("Secrets stay out.", "deny-env"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyDenied) as info:
        bb.guard("filesystem.read", ".env")
    assert info.value.policy_id == "deny-env" and info.value.policy_version == 3
    assert info.value.reason == "Secrets stay out." and "Secrets stay out." in str(info.value)
    assert ".env" not in str(info.value)
    assert not isinstance(info.value, (ApprovalDenied, ApprovalExpired))
    bb.shutdown()


def test_the_exceptions_are_policy_errors_and_survive_pickling() -> None:
    import pickle

    for error in (
        PolicyDenied("p", 2, "why"),
        ApprovalDenied("p", 2, "why"),
        ApprovalExpired(None, 0, "why"),
    ):
        again = pickle.loads(pickle.dumps(error))  # noqa: S301
        assert type(again) is type(error) and again.reason == "why"
        assert isinstance(again, PolicyDenied) and isinstance(again, PolicyError)
    unavailable = pickle.loads(pickle.dumps(PolicyUnavailable("policy_timeout", "x.y")))  # noqa: S301
    assert unavailable.reason == "policy_timeout" and isinstance(unavailable, PolicyError)
    assert issubclass(PolicyError, Exception)


def test_each_call_has_its_own_idempotency_key(srv: PolicyServer) -> None:
    bb = client(srv)
    with bb.run("t"):
        bb.guard("a.b")
        bb.guard("a.b")
    keys = [r.body["idempotency_key"] for r in srv.of("decide") if r.body]
    assert len(set(keys)) == 2
    bb.shutdown()


def test_a_429_is_retried_once_with_the_same_key(srv: PolicyServer) -> None:
    srv.queue("decide", R(429, {"error": {"code": "RATE_LIMITED"}}, {"Retry-After": "0"}), allow())
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("a.b").via == "policy"
    sent = srv.of("decide")
    assert len(sent) == 2 and sent[0].body and sent[1].body
    assert sent[0].body["idempotency_key"] == sent[1].body["idempotency_key"]
    bb.shutdown()


# -- approvals -------------------------------------------------------------------------------------


def test_approved_action_consumes_with_the_full_intent(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state("REQUESTED"), state("APPROVED"))
    bb = client(srv)
    with bb.run("t") as run:
        decision = bb.guard("github.push", "origin main", None, {"branch": "main", "force": False})
    assert decision.via == "approval" and decision.approval_id == "apr_01"
    assert decision.policy_id == "gate-push" and decision.decision == "allow"
    asked = srv.of("decide")[0].body
    spent = srv.of("consume")
    assert asked is not None and len(spent) == 1 and spent[0].body is not None
    assert spent[0].path == "/v1/approvals/apr_01/consume"
    sent = spent[0].body["intent"]
    assert set(spent[0].body) == {"intent"}
    # the same intent, field for field: the server recomputes the hash and the SDK never does
    assert sent == {
        k: asked[k]
        for k in ("run_id", "agent_id", "capability", "resource", "risk_class", "arguments")
    }
    assert set(sent) == {"run_id", "agent_id", "capability", "resource", "risk_class", "arguments"}
    assert sent["run_id"] == run.run_id and sent["arguments"] == {"branch": "main", "force": False}
    assert len(srv.of("wait")) == 2
    assert "timeout=0.05" in srv.of("wait")[0].path
    bb.shutdown()


def test_a_denied_approval_raises_approval_denied(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state("DENIED"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(ApprovalDenied) as info:
        bb.guard("github.push", "origin main")
    assert isinstance(info.value, PolicyDenied) and info.value.policy_id == "gate-push"
    assert srv.of("consume") == []
    bb.shutdown()


@pytest.mark.parametrize("final", ["EXPIRED", "CANCELLED"])
def test_expiry_denies_by_default(srv: PolicyServer, fast: None, final: str) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state(final))
    bb = client(srv)
    with bb.run("t"), pytest.raises(ApprovalExpired) as info:
        bb.guard("github.push", "origin main")
    assert isinstance(info.value, PolicyDenied) and srv.of("consume") == []
    bb.shutdown()


@pytest.mark.parametrize("final", ["EXPIRED", "CANCELLED"])
def test_expiry_allow_proceeds_and_records_a_warning(
    srv: PolicyServer, fast: None, final: str
) -> None:
    srv.queue("decide", require(on_expiry="allow"))
    srv.queue("wait", state(final, on_expiry="allow"))
    bb = client(srv)
    with bb.run("t"):
        decision = bb.guard("github.push", "origin main")
    assert decision.via == "expiry_allow" and decision.decision == "allow"
    found = warnings_of(srv, bb)
    assert [w["attributes"]["policy.reason"] for w in found] == ["approval_expired_allowed"]
    assert srv.of("consume") == []
    bb.shutdown()


def test_the_wait_response_may_override_on_expiry(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require(on_expiry="deny"))
    srv.queue("wait", state("EXPIRED", on_expiry="allow"))
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("github.push", "origin main").via == "expiry_allow"
    bb.shutdown()


@pytest.mark.parametrize(
    "refusal",
    [
        refused("APPROVAL_NOT_APPROVED"),
        refused("APPROVAL_EXPIRED"),
        refused("APPROVAL_CONSUMED"),
        refused("APPROVAL_INTENT_MISMATCH"),
        refused("APPROVAL_POLICY_DENIED"),
        refused("APPROVAL_NOT_FOUND", 404),
        R(500, {"error": {"code": "INTERNAL"}}),
        R(503),
        R(200, b"not json"),
        R(drop=True),  # a lost response: the server may have spent the approval
        R(hold=3.0),  # a timeout
    ],
    ids=lambda r: f"{r.status}-{'drop' if r.drop else 'hold' if r.hold else 'reply'}",
)
def test_any_consume_failure_is_a_denial_and_is_not_retried(
    srv: PolicyServer, fast: None, refusal: R
) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state("APPROVED"))
    srv.queue("consume", refusal)
    bb = client(srv, policy_timeout=0.4)
    started = time.monotonic()
    with bb.run("t"), pytest.raises(PolicyDenied) as info:
        bb.guard("github.push", "origin main")
    assert time.monotonic() - started < 2.5
    assert not isinstance(info.value, (ApprovalDenied, ApprovalExpired))
    assert len(srv.of("consume")) == 1  # one attempt: a retry would be a replay window
    bb.shutdown()


def test_an_approval_already_spent_is_never_consumed_again(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state("APPROVED", consumed=True))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyDenied):
        bb.guard("github.push", "origin main")
    assert srv.of("consume") == []
    bb.shutdown()


def test_consume_must_name_the_same_approval(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require())
    srv.queue("wait", state("APPROVED"))
    srv.queue("consume", consumed("apr_other"))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyDenied):
        bb.guard("github.push", "origin main")
    bb.shutdown()


# -- bounded waits ---------------------------------------------------------------------------------


def test_the_wait_ends_by_the_approvals_expiry_plus_the_margin(
    srv: PolicyServer, fast: None
) -> None:
    srv.queue("decide", require(expires_in=0.3))
    bb = client(srv)  # the server keeps answering REQUESTED
    started = time.monotonic()
    with bb.run("t"), pytest.raises(ApprovalExpired):
        bb.guard("github.push", "origin main")
    elapsed = time.monotonic() - started
    assert 0.3 <= elapsed < 0.3 + 0.15 + 1.0  # expiry + margin + one round of slack
    assert len(srv.of("wait")) >= 3  # bounded rounds, not one long call
    assert len(srv.of("cancel")) == 1  # the request is withdrawn on the way out
    bb.shutdown()


def test_the_absolute_cap_beats_a_distant_expiry(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require(expires_in=3600))
    bb = client(srv, policy_wait_max=0.3)
    started = time.monotonic()
    with bb.run("t"), pytest.raises(ApprovalExpired):
        bb.guard("github.push", "origin main")
    assert time.monotonic() - started < 0.3 + 1.0
    assert len(srv.of("cancel")) == 1
    bb.shutdown()


def test_a_wait_that_keeps_failing_is_unavailable_never_open(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require(expires_in=30))
    srv.script["wait"].extend([R(drop=True)] * 20)
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable) as info:
        bb.guard("tool.x", "r", "R0", on_error="open")  # even an explicit open does not apply
    assert info.value.reason == "policy_service_unreachable"
    assert len(srv.of("wait")) == policy_module.MAX_WAIT_FAILURES + 1
    assert len(srv.of("cancel")) == 1
    assert [w["attributes"]["policy.reason"] for w in warnings_of(srv, bb)] == [
        "policy_service_unreachable"
    ]
    bb.shutdown()


def test_one_flaky_round_is_survived(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require())
    srv.queue("wait", R(503), R(drop=True), state("APPROVED"))
    bb = client(srv)
    with bb.run("t"):
        assert bb.guard("github.push", "origin main").via == "approval"
    bb.shutdown()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_wait_the_key_may_not_make_is_final(srv: PolicyServer, fast: None, status: int) -> None:
    srv.queue("decide", require())
    srv.queue("wait", R(status, {"error": {"code": "NO"}}))
    bb = client(srv)
    with bb.run("t"), pytest.raises(PolicyUnavailable):
        bb.guard("github.push", "origin main", "R0")
    assert len(srv.of("wait")) == 1
    bb.shutdown()


def test_backoff_is_jittered_and_bounded() -> None:
    from blackbox.config import Config
    from blackbox.stats import Stats

    engine = PolicyGuard(Config(mode="offline"), Stats())
    for failures in range(1, 12):
        for _ in range(20):
            assert 0 <= engine._backoff(failures, None) <= 2.0
    assert engine._backoff(3, 1.5) == 1.5  # the server's Retry-After wins


def test_keyboard_interrupt_cancels_the_approval_and_propagates(
    srv: PolicyServer, fast: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv.queue("decide", require())
    bb = client(srv)
    real = policy_transport.PolicyTransport.request

    def interrupted(self: Any, method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        if path.endswith("/wait") or "/wait?" in path:
            raise KeyboardInterrupt
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(policy_transport.PolicyTransport, "request", interrupted)
    started = time.monotonic()
    with bb.run("t"), pytest.raises(KeyboardInterrupt):
        bb.guard("github.push", "origin main")
    assert len(srv.of("cancel")) == 1 and srv.of("cancel")[0].path == "/v1/approvals/apr_01"
    assert time.monotonic() - started < 2
    bb.shutdown()


def test_system_exit_is_propagated_unchanged(
    srv: PolicyServer, fast: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv.queue("decide", require())
    bb = client(srv)
    real = policy_transport.PolicyTransport.request

    def exiting(self: Any, method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        if "/wait?" in path:
            raise SystemExit(3)
        return real(self, method, path, *args, **kwargs)

    monkeypatch.setattr(policy_transport.PolicyTransport, "request", exiting)
    with bb.run("t"), pytest.raises(SystemExit) as info:
        bb.guard("github.push", "origin main")
    assert info.value.code == 3 and len(srv.of("cancel")) == 1
    bb.shutdown()


# -- asyncio ---------------------------------------------------------------------------------------


def test_aguard_allows_denies_and_waits(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", allow(), deny(), require())
    srv.queue("wait", state("APPROVED"))
    bb = client(srv)

    async def main() -> None:
        async with bb.run("t"):
            assert (await bb.aguard("a.b")).via == "policy"
            with pytest.raises(PolicyDenied):
                await bb.aguard("a.b")
            assert (await blackbox.aguard("github.push", "origin main")).via == "approval"

    asyncio.run(main())
    bb.shutdown()


def test_cancelling_aguard_withdraws_the_pending_approval(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", require(expires_in=60))
    bb = client(srv)

    async def main() -> None:
        async with bb.run("t"):
            task = asyncio.ensure_future(bb.aguard("github.push", "origin main"))
            while not srv.of("wait"):
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(200):
                if srv.of("cancel"):
                    break
                await asyncio.sleep(0.02)

    asyncio.run(main())
    assert len(srv.of("cancel")) == 1
    bb.shutdown()


# -- threads ---------------------------------------------------------------------------------------


def test_many_threads_guard_at_once(srv: PolicyServer) -> None:
    bb = client(srv)
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            with bb.run(f"t{i}"):
                for j in range(5):
                    if (i + j) % 7 == 0:
                        srv.queue("decide", deny())
                    try:
                        bb.guard("a.b", f"r{i}", "R1", {"i": i, "j": j})
                        outcome = "allow"
                    except PolicyDenied:
                        outcome = "deny"
                    with lock:
                        results.append(outcome)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and len(results) == 160
    assert len({r.body["idempotency_key"] for r in srv.of("decide") if r.body}) == 160
    assert len(srv.of("failmodes")) == 1  # the table is fetched once, not once per thread
    bb.shutdown()


def test_decisions_are_made_where_the_caller_is(srv: PolicyServer) -> None:
    """No background thread is started for guard(): the call runs on the calling thread."""
    bb = client(srv)
    before = {t.ident for t in threading.enumerate()}
    with bb.run("t"):
        bb.guard("a.b")
    started = {t.name for t in threading.enumerate() if t.ident not in before}
    assert not any("policy" in name or "guard" in name for name in started)
    bb.shutdown()


def test_decide_and_consume_use_the_configured_timeout(srv: PolicyServer, fast: None) -> None:
    srv.queue("decide", R(hold=2.0))
    bb = client(srv, policy_timeout=0.25)
    started = time.monotonic()
    with bb.run("t"):
        decision = bb.guard("a.b", risk_class="R0")
    assert decision.via == "fail_open" and time.monotonic() - started < 1.0
    bb.shutdown()


# -- the approval callback and the async pool --------------------------------------------------------


def test_the_approval_callback_gets_the_id_once_and_no_content(
    srv: PolicyServer, fast: None
) -> None:
    seen: list[Any] = []
    srv.queue("decide", require(approval_id="apr_77", expires_in=120))
    srv.queue("wait", state("REQUESTED", "apr_77"), state("APPROVED", "apr_77"))
    srv.queue("consume", consumed("apr_77"))
    bb = client(srv, policy_on_approval=seen.append)
    with bb.run("t"):
        bb.guard("github.push", "SECRET-RESOURCE", None, {"branch": "main", "x": "SECRET-ARG"})
    assert len(seen) == 1
    pending = seen[0]
    assert (pending.approval_id, pending.policy_id, pending.policy_version) == (
        "apr_77",
        "gate-push",
        3,
    )
    assert 100 < pending.expires_in <= 121 and "SECRET" not in repr(pending)
    bb.shutdown()


def test_a_callback_that_raises_is_contained_and_an_interrupt_in_it_withdraws(
    srv: PolicyServer, fast: None
) -> None:
    def boom(_pending: Any) -> None:
        raise RuntimeError("host bug")

    srv.queue("decide", require())
    srv.queue("wait", state("APPROVED"))
    bb = client(srv, policy_on_approval=boom)
    with bb.run("t"):
        assert bb.guard("github.push", "origin main").via == "approval"

    def interrupt(_pending: Any) -> None:
        raise KeyboardInterrupt

    srv.queue("decide", require(approval_id="apr_02"))
    bb2 = client(srv, policy_on_approval=interrupt)
    with bb2.run("t"), pytest.raises(KeyboardInterrupt):
        bb2.guard("github.push", "origin main")
    assert [r.path for r in srv.of("cancel")] == ["/v1/approvals/apr_02"]
    bb.shutdown()
    bb2.shutdown()


def test_pending_approvals_do_not_starve_the_loops_default_executor(
    srv: PolicyServer, fast: None
) -> None:
    """Forty waiting approvals (more than the default executor has threads) leave to_thread free."""
    for index in range(40):
        srv.queue("decide", require(approval_id=f"apr_{index:02d}", expires_in=60))
    bb = client(srv, policy_wait_round=0.2)

    async def main() -> int:
        async with bb.run("t"):
            tasks = [
                asyncio.ensure_future(bb.aguard("github.push", f"origin b{i}")) for i in range(40)
            ]
            while len(srv.of("wait")) < 40:
                await asyncio.sleep(0.02)
            free = await asyncio.wait_for(asyncio.to_thread(lambda: 7), 2.0)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return free

    assert asyncio.run(main()) == 7
    names = {t.name for t in threading.enumerate()}
    assert any(n.startswith("blackbox-guard") for n in names)
    bb.shutdown()
