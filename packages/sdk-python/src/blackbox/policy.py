"""Cooperative policy enforcement: `guard()` asks the server before a sensitive action (D1, D3).

    decision = bb.guard("shell.execute", "git", "R3", {"category": "NETWORK"})

`allow` returns a `Decision`; `deny` raises `PolicyDenied`; `require_approval` waits (long polling,
bounded) for a person and then spends the approval for exactly this intent, once. The guarantee is
cooperative: it stops an agent that asks, not one that bypasses the SDK. Policy checks are off
unless `policy_mode="enforce"` (or `BLACKBOX_POLICY=enforce`); then `guard()` makes network calls
on the calling thread and nothing else (no background thread).

INV-4: `guard()` raises only the documented policy exceptions (`PolicyDenied` and its subclasses,
`PolicyUnavailable`) and, for `KeyboardInterrupt` or `SystemExit` during an approval wait, those
unchanged after a best-effort cancel. Every other failure, bugs in this module included, follows
the fail mode and records a `policy.warning` whose reason is a word from a closed vocabulary. It is
bounded: a decision call by `policy_timeout` (plus one `Retry-After` pause of at most 2 s and one
retry), a wait by the approval's expiry plus a margin and `policy_wait_max`. Arguments, resources
and server error text are never logged and never put in a warning.

A consume that did not finish (network error, 5xx, timeout, any 409) is a denial: the response may
have been lost after the server spent the approval, and an idempotent consume would be a replay
window (ADR-141 point 6, KI-145), so the action waits for a fresh approval.
"""

import asyncio
import contextvars
import functools
import inspect
import logging
import os
import random
import re
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, TypeVar, cast

from blackbox.context import _current_run, _current_span
from blackbox.policy_failmodes import (
    CLOSED,
    MODES,
    FailModeTable,
    default_mode,
    override_for,
)
from blackbox.policy_intent import (
    REASON_INVALID,
    IntentRejected,
    build_intent,
)
from blackbox.policy_transport import (
    REASON_ERROR,
    PolicyTransport,
    Reply,
    TransportFailure,
)
from blackbox.stats import Stats

if TYPE_CHECKING:
    from blackbox.client import BlackBox, Run
    from blackbox.config import Config

log = logging.getLogger("blackbox")
F = TypeVar("F", bound=Callable[..., Any])

# The closed vocabulary of `policy.warning` reasons. Never content, never error text.
REASON_UNREACHABLE = "policy_service_unreachable"
REASON_TIMEOUT = "policy_timeout"
REASON_UNAUTHORISED = "unauthorised"
REASON_INTERNAL = "internal_error"
REASON_CONSUME_FAILED = "approval_consume_failed"
REASON_EXPIRY_ALLOWED = "approval_expired_allowed"
WARNING_REASONS = frozenset(
    {
        "policy_service_unreachable",
        "policy_timeout",
        "policy_error",
        "unauthorised",
        "intent_too_large",
        "invalid_intent",
        "internal_error",
        "approval_consume_failed",
        "approval_expired_allowed",
    }
)

WAIT_MARGIN = 5.0  # seconds past the approval's expiry before the SDK stops asking
ROUND_SLACK = 5.0  # the socket allowance of a wait round beyond the round the server holds
MAX_WAIT_FAILURES = 5  # consecutive failed rounds before an approval wait is abandoned
MAX_RETRY_AFTER = 2.0  # one pause this long is honoured on a 429 from the decision call
TABLE_REFRESH_SECONDS = 300.0
TABLE_RETRY_SECONDS = 60.0
TABLE_TIMEOUT = 1.0
CANCEL_TIMEOUT = 1.0
CANCELLABLE_ROUND = 5.0  # async callers get shorter rounds so a cancelled task stops quickly
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RFC3339 = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)[Tt ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|z|[+-]\d\d:?\d\d)?$"
)


# -- results and exceptions ----------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """An allowed action. A denial raises instead.

    `via` says why it may proceed: `policy` (the policy allows it), `approval` (a person approved
    exactly this action and the approval was spent), `fail_open` (the service could not be asked),
    `expiry_allow` (the approval expired and the rule says proceed), `disabled` (policy checks are
    off; no network call was made).
    """

    decision: str
    policy_id: str | None = None
    policy_version: int = 0
    reason: str = ""
    approval_id: str | None = None
    via: str = "policy"


class PolicyError(Exception):
    """Base of every exception `guard()` raises."""


class PolicyDenied(PolicyError):
    """The action must not happen. `reason` is the policy author's own words, not the action."""

    def __init__(self, policy_id: str | None, policy_version: int, reason: str) -> None:
        self.policy_id = policy_id
        self.policy_version = policy_version
        self.reason = reason
        super().__init__(f"denied by policy ({policy_id or 'default'} v{policy_version}): {reason}")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.policy_id, self.policy_version, self.reason))


class ApprovalDenied(PolicyDenied):
    """A person denied the approval."""


class ApprovalExpired(PolicyDenied):
    """The approval expired or was cancelled, or the wait ended, and the rule says deny."""


class PolicyUnavailable(PolicyError):
    """The service could not be asked and the fail mode is closed (or an approval wait broke)."""

    def __init__(self, reason: str, capability: str = "") -> None:
        self.reason = reason if reason in WARNING_REASONS else REASON_INTERNAL
        self.capability = capability
        super().__init__(f"policy service unavailable ({self.reason}); failing closed")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.reason, self.capability))


@dataclass(frozen=True)
class ApprovalPending:
    """What `policy_on_approval` receives when a person has to approve an action: enough to show
    or forward the approval id. No resource, argument or command."""

    approval_id: str
    policy_id: str | None
    policy_version: int
    reason: str
    expires_in: float  # seconds the approval stays usable, by the server's clock


DISABLED = Decision("allow", None, 0, "", None, "disabled")


# -- parsing what the server says ----------------------------------------------------------------


def _short(value: Any, limit: int = 300) -> str:
    return _CONTROL.sub(" ", value)[:limit] if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_timestamp(value: Any) -> float | None:
    """A Unix time from an RFC 3339 string (3.10's fromisoformat is too strict for 'Z')."""
    if not isinstance(value, str):
        return None
    found = _RFC3339.match(value.strip())
    if found is None:
        return None
    year, month, day, hour, minute, second, fraction, zone = found.groups()
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            int((fraction or "0")[:6].ljust(6, "0")),
            tzinfo=timezone.utc,
        )
        if zone and zone not in ("Z", "z"):
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return moment.timestamp()
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class _Verdict:
    decision: str
    policy_id: str | None
    policy_version: int
    reason: str
    approval_id: str | None
    expires_at: float | None
    on_expiry: str


MAX_CLOCK_SKEW = 600.0  # seconds of server/client clock difference the wait budget believes
MAX_COUNTER = 2**53  # a version beyond this is not a version
INVALID = "invalid"  # a 2xx reply whose decision cannot be acted on


def _parse_verdict(body: dict[str, Any] | None) -> _Verdict | None:
    """Read `decision` FIRST. A deny is a deny whatever else is malformed; a decision that is
    unknown, or a require_approval without a usable approval, is `INVALID` and the caller fails
    CLOSED for any risk class (a person was asked for, or the server said something this client
    cannot interpret). Only a body that is not an object at all is a transport-class failure."""
    if not isinstance(body, dict):
        return None
    decision = body.get("decision")
    version = _int(body.get("policy_version"))
    policy_id = body.get("policy_id")
    if version is None or version > MAX_COUNTER:
        version = 0
    safe_id = _short(policy_id, 128) if isinstance(policy_id, str) else None
    reason = _short(body.get("reason"))
    if decision not in ("allow", "deny", "require_approval"):
        return _Verdict(INVALID, None, 0, "", None, None, "deny")
    if decision != "require_approval":
        return _Verdict(decision, safe_id, version, reason, None, None, "deny")
    on_expiry = body.get("on_expiry")
    approval_id = body.get("approval_id")
    expires_at = parse_timestamp(body.get("expires_at"))
    if not (isinstance(approval_id, str) and _APPROVAL_ID.fullmatch(approval_id)) or (
        expires_at is None
    ):
        return _Verdict(INVALID, safe_id, version, reason, None, None, "deny")
    return _Verdict(
        decision, safe_id, version, reason, approval_id, expires_at,
        on_expiry if on_expiry in ("deny", "allow") else "deny",
    )  # fmt: skip


def _reason_of_status(status: int) -> str:
    if status in (401, 403):
        return REASON_UNAUTHORISED
    if status == 413:
        return "intent_too_large"
    if status == 422:
        return REASON_INVALID
    return REASON_ERROR


class _Progress:
    """What an interrupted or crashed guard must clean up: the approval it is waiting on."""

    def __init__(self) -> None:
        self.approval_id: str | None = None
        self.spent = False


class _Abandoned(PolicyError):
    """An async caller was cancelled while an approval was pending."""


# -- the guard -----------------------------------------------------------------------------------


class PolicyGuard:
    """One per `BlackBox`. Holds the cached fail-mode table; every call is otherwise independent."""

    def __init__(
        self,
        config: "Config",
        stats: Stats,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._c, self._stats, self._clock, self._wall = config, stats, clock, wall
        self._sleeper = sleep
        self._rng = random.Random()  # noqa: S311  (backoff jitter, not security)
        self._lock = threading.Lock()
        self._table: FailModeTable | None = None
        self._table_at = -1e18
        self._table_next = -1e18  # the earliest next attempt to fetch the table
        self._refreshing = False
        key = config.api_key
        self._transport = (
            PolicyTransport(config.endpoint, key, clock) if config.mode == "http" and key else None
        )

    # -- entry point ---------------------------------------------------------------------------

    def guard(
        self,
        run: "Run | None",
        capability: Any,
        resource: Any = None,
        risk_class: Any = None,
        arguments: Any = None,
        on_error: Any = None,
        cancel: threading.Event | None = None,
    ) -> Decision:
        if self._c.policy_mode != "enforce":
            return DISABLED
        progress = _Progress()
        mode_hint = on_error if on_error in MODES else None
        try:
            return self._guard(
                run, capability, resource, risk_class, arguments, mode_hint, cancel, progress
            )
        except PolicyError:
            raise
        except Exception as exc:  # INV-4: a bug here must follow the fail mode, never escape
            self._stats.add("internal_errors")
            log.debug("blackbox: policy guard bug contained (%s)", type(exc).__name__)
            self._cancel_quietly(progress)
            try:
                return self._fail(run, capability, resource, risk_class, mode_hint, REASON_INTERNAL)
            except PolicyError:
                raise
            except Exception:
                self._stats.add("internal_errors")
                if default_mode(risk_class if isinstance(risk_class, str) else None) == CLOSED:
                    raise PolicyUnavailable(REASON_INTERNAL) from None
                return Decision("allow", None, 0, "", None, "fail_open")

    def fail(
        self,
        run: "Run | None",
        capability: Any,
        risk_class: Any,
        on_error: Any,
        reason: str,
    ) -> Decision:
        """Apply the fail mode for a call whose intent could not even be built (a decorator whose
        resource or arguments callable raised). Same contract as `guard`."""
        if self._c.policy_mode != "enforce":
            return DISABLED
        hint = on_error if on_error in MODES else None
        try:
            return self._fail(run, capability, "", risk_class, hint, reason)
        except PolicyError:
            raise
        except Exception:
            self._stats.add("internal_errors")
            if default_mode(risk_class if isinstance(risk_class, str) else None) == CLOSED:
                raise PolicyUnavailable(reason) from None
            return Decision("allow", None, 0, "", None, "fail_open")

    def _guard(
        self,
        run: "Run | None",
        capability: Any,
        resource: Any,
        risk_class: Any,
        arguments: Any,
        on_error: str | None,
        cancel: threading.Event | None,
        progress: _Progress,
    ) -> Decision:
        if run is None:  # nothing to bind the decision to: ask nothing, follow the fail mode
            return self._fail(None, capability, resource, risk_class, on_error, REASON_INVALID)
        try:
            intent = build_intent(
                run_id=run.run_id,
                agent_id=_agent_of(run),
                capability=capability,
                resource=resource,
                risk_class=risk_class,
                arguments=arguments,
            )
        except IntentRejected as rejected:
            return self._fail(run, capability, resource, risk_class, on_error, rejected.reason)
        try:
            reply = self._decide(intent, run.trace_id)
        except TransportFailure as failure:
            return self._fail(run, capability, resource, risk_class, on_error, failure.reason)
        if not 200 <= reply.status < 300:
            return self._fail(
                run, capability, resource, risk_class, on_error, _reason_of_status(reply.status)
            )
        verdict = _parse_verdict(reply.body)
        if verdict is None:
            return self._fail(run, capability, resource, risk_class, on_error, REASON_ERROR)
        if verdict.decision == INVALID:
            name = capability if isinstance(capability, str) else ""
            self._warn(run, name, REASON_ERROR)
            self._stats.add("policy_fail_closed")
            raise PolicyUnavailable(REASON_ERROR, name if _safe_name(name) else "")
        self._stats.add("policy_checks")
        self._refresh_table(verdict.policy_version)
        if verdict.decision == "allow":
            return Decision("allow", verdict.policy_id, verdict.policy_version, verdict.reason)
        if verdict.decision == "deny":
            self._stats.add("policy_denied")
            raise PolicyDenied(verdict.policy_id, verdict.policy_version, verdict.reason)
        progress.approval_id = verdict.approval_id
        self._stats.add("policy_approvals")
        return self._await_approval(run, intent, verdict, reply, cancel, progress)

    # -- the decision call ---------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        timeout: float,
    ) -> Reply:
        if self._transport is None:  # offline, local or disabled mode, or no key: nobody to ask
            raise TransportFailure(REASON_UNREACHABLE)
        return self._transport.request(method, path, body, timeout)

    def _decide(self, intent: dict[str, Any], trace_id: str) -> Reply:
        body: dict[str, Any] = {**intent, "idempotency_key": "g" + secrets.token_hex(15)}
        if isinstance(trace_id, str) and re.fullmatch(r"trc_[0-9A-HJKMNP-TV-Z]{26}", trace_id):
            body["trace_id"] = trace_id
        timeout = self._c.policy_timeout
        reply = self._call("POST", "/v1/policy/decide", body, timeout)
        wait = reply.retry_after
        if reply.status == 429 and wait is not None and wait <= MAX_RETRY_AFTER:
            self._pause(wait, None)  # once; the same idempotency key
            reply = self._call("POST", "/v1/policy/decide", body, timeout)
        return reply

    # -- approvals -----------------------------------------------------------------------------

    def _await_approval(
        self,
        run: "Run",
        intent: dict[str, Any],
        verdict: _Verdict,
        decided: Reply,
        cancel: threading.Event | None,
        progress: _Progress,
    ) -> Decision:
        assert verdict.approval_id is not None and verdict.expires_at is not None
        approval = verdict.approval_id
        capability = intent["capability"]
        local_now = self._wall()
        server_now = decided.server_time if decided.server_time is not None else local_now
        if abs(server_now - local_now) > MAX_CLOCK_SKEW:
            # A server Date far from ours must not shrink a live approval's wait budget (and so
            # cancel it): past the bound, trust our own clock.
            server_now = local_now
        left_on_approval = max(0.0, verdict.expires_at - server_now)
        budget = min(left_on_approval + WAIT_MARGIN, self._c.policy_wait_max)
        deadline = self._clock() + budget
        round_cap = min(self._c.policy_wait_round, CANCELLABLE_ROUND if cancel else 25.0)
        failures = 0
        last_reason = REASON_ERROR
        try:
            self._announce(verdict, left_on_approval)
            while True:
                if cancel is not None and cancel.is_set():
                    self._cancel_quietly(progress)
                    raise _Abandoned("cancelled")
                left = deadline - self._clock()
                if left <= 0:
                    break
                seconds = max(0.0, min(round_cap, left))
                reply: Reply | None = None
                try:
                    reply = self._call(
                        "GET",
                        f"/v1/approvals/{approval}/wait?timeout={seconds:.2f}",
                        None,
                        seconds + ROUND_SLACK,
                    )
                except TransportFailure as failure:
                    failures, last_reason = failures + 1, failure.reason
                retry_after: float | None = None
                if reply is not None:
                    kind = self._read_wait(reply)
                    if kind == "terminal":  # this key cannot wait on this approval: final
                        reason = _reason_of_status(reply.status)
                        self._cancel_quietly(progress)
                        self._warn(run, capability, reason)
                        self._stats.add("policy_fail_closed")
                        raise PolicyUnavailable(reason, capability)
                    if kind == "pending":
                        failures = 0
                        continue
                    if kind == "decided":
                        return self._settle(run, intent, verdict, reply, progress)
                    failures, last_reason = failures + 1, REASON_ERROR
                    retry_after = reply.retry_after
                if failures > MAX_WAIT_FAILURES:
                    raise _WaitBroken(last_reason)
                self._pause(self._backoff(failures, retry_after), cancel, deadline)
        except _WaitBroken as broken:
            # A wait that cannot be completed never falls open: the rule asked for a person.
            self._cancel_quietly(progress)
            self._warn(run, capability, broken.reason)
            self._stats.add("policy_fail_closed")
            raise PolicyUnavailable(broken.reason, capability) from None
        except (KeyboardInterrupt, SystemExit):
            self._cancel_quietly(progress)
            raise
        self._cancel_quietly(progress)
        raise ApprovalExpired(
            verdict.policy_id, verdict.policy_version, "The wait for approval ended undecided."
        )

    def _announce(self, verdict: _Verdict, expires_in: float) -> None:
        """Tell the host an approval is pending (its callback runs here, on the calling thread, so
        it must return quickly). A callback that raises is contained; an interrupt is not."""
        callback = self._c.policy_on_approval
        if callback is None or verdict.approval_id is None:
            return
        try:
            callback(
                ApprovalPending(
                    verdict.approval_id,
                    verdict.policy_id,
                    verdict.policy_version,
                    verdict.reason,
                    expires_in,
                )
            )
        except Exception:
            self._stats.add("internal_errors")

    @staticmethod
    def _read_wait(reply: Reply) -> str:
        """pending | decided | retry | terminal for one wait round's reply."""
        body = reply.body
        if reply.status == 200 and body is not None:
            state = body.get("state")
            if state == "REQUESTED":
                return "pending"
            if state in ("APPROVED", "DENIED", "EXPIRED", "CANCELLED"):
                return "decided"
            return "retry"
        if reply.status in (401, 403, 404):
            return "terminal"
        return "retry"  # 429, 5xx, anything unexpected: transient, counted

    def _settle(
        self,
        run: "Run",
        intent: dict[str, Any],
        verdict: _Verdict,
        reply: Reply,
        progress: _Progress,
    ) -> Decision:
        body = reply.body or {}
        state = body["state"]
        capability = intent["capability"]
        if state == "APPROVED":
            if body.get("consumed") is True:  # spent by someone else: never reusable
                raise PolicyDenied(verdict.policy_id, verdict.policy_version, CONSUME_TEXT)
            return self._consume(run, intent, verdict, progress)
        if state == "DENIED":
            self._stats.add("policy_denied")
            raise ApprovalDenied(verdict.policy_id, verdict.policy_version, verdict.reason)
        if state == "CANCELLED":
            # "The requester withdrew" is never "time ran out": on_expiry does not apply.
            self._stats.add("policy_denied")
            raise ApprovalDenied(
                verdict.policy_id, verdict.policy_version, "The approval was cancelled."
            )
        on_expiry = body.get("on_expiry") if body.get("on_expiry") in ("deny", "allow") else None
        if (on_expiry or verdict.on_expiry) == "allow":
            self._warn(run, capability, REASON_EXPIRY_ALLOWED)
            return Decision(
                "allow",
                verdict.policy_id,
                verdict.policy_version,
                verdict.reason,
                verdict.approval_id,
                "expiry_allow",
            )
        self._stats.add("policy_denied")
        raise ApprovalExpired(
            verdict.policy_id, verdict.policy_version, f"The approval {state.lower()}."
        )

    def _consume(
        self, run: "Run", intent: dict[str, Any], verdict: _Verdict, progress: _Progress
    ) -> Decision:
        """Spend the approval for the whole intent. Exactly one attempt: see the module note."""
        progress.spent = True  # from here an interrupt must not try to cancel
        capability = intent["capability"]
        try:
            reply = self._call(
                "POST",
                f"/v1/approvals/{verdict.approval_id}/consume",
                {"intent": intent},
                self._c.policy_timeout,
            )
        except TransportFailure as failure:
            self._warn(run, capability, failure.reason)
            raise PolicyDenied(verdict.policy_id, verdict.policy_version, CONSUME_TEXT) from None
        body = reply.body or {}
        if 200 <= reply.status < 300 and body.get("approval_id") == verdict.approval_id:
            return Decision(
                "allow",
                verdict.policy_id,
                verdict.policy_version,
                verdict.reason,
                verdict.approval_id,
                "approval",
            )
        if reply.status != 409:  # a refusal is the server working; anything else is a warning
            self._warn(run, capability, REASON_CONSUME_FAILED)
        raise PolicyDenied(verdict.policy_id, verdict.policy_version, CONSUME_TEXT)

    def _cancel_quietly(self, progress: _Progress) -> None:
        """Withdraw the waiting request (best effort, one second); never raises."""
        approval = progress.approval_id
        if approval is None or progress.spent:
            return
        try:
            progress.approval_id = None
            self._call("DELETE", f"/v1/approvals/{approval}", None, CANCEL_TIMEOUT)
        except Exception:  # noqa: S110  (the request expires on its own)
            pass

    # -- waiting helpers -----------------------------------------------------------------------

    def _backoff(self, failures: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        ceiling = min(2.0, 0.2 * (2 ** max(0, failures - 1)))
        return self._rng.uniform(0, ceiling)  # full jitter

    def _pause(
        self, seconds: float, cancel: threading.Event | None, deadline: float | None = None
    ) -> None:
        if deadline is not None:
            seconds = min(seconds, max(0.0, deadline - self._clock()))
        seconds = max(0.0, seconds)
        if self._sleeper is not None:
            self._sleeper(seconds)
        elif cancel is not None:
            cancel.wait(seconds)
        else:
            threading.Event().wait(seconds)

    # -- failing -------------------------------------------------------------------------------

    def _mode(self, capability: Any, resource: Any, risk_class: Any, on_error: str | None) -> str:
        if on_error in MODES:
            return on_error
        risk = risk_class if isinstance(risk_class, str) else None
        name = capability if isinstance(capability, str) else ""
        found = override_for(self._c.policy_fail_modes, name)
        if found is not None:
            return found
        with self._lock:
            table = self._table
        if table is not None:
            return table.mode_for(name, resource if isinstance(resource, str) else "", risk)
        return default_mode(risk)

    def _fail(
        self,
        run: "Run | None",
        capability: Any,
        resource: Any,
        risk_class: Any,
        on_error: str | None,
        reason: str,
    ) -> Decision:
        reason = reason if reason in WARNING_REASONS else REASON_INTERNAL
        mode = self._mode(capability, resource, risk_class, on_error)
        name = capability if isinstance(capability, str) else ""
        self._warn(run, name, reason)
        if mode == CLOSED:
            self._stats.add("policy_fail_closed")
            raise PolicyUnavailable(reason, name if _safe_name(name) else "")
        self._stats.add("policy_fail_open")
        return Decision("allow", None, 0, "", None, "fail_open")

    def _warn(self, run: "Run | None", capability: str, reason: str) -> None:
        """Record `policy.warning` with fixed words only. Never raises."""
        if run is None:
            return
        try:
            run.event(
                "policy.warning",
                {
                    "policy.capability": capability if _safe_name(capability) else "invalid",
                    "policy.reason": reason if reason in WARNING_REASONS else REASON_INTERNAL,
                },
            )
        except Exception:
            self._stats.add("internal_errors")

    # -- the cached fail-mode table ------------------------------------------------------------

    def _refresh_table(self, server_version: int) -> None:
        """Opportunistic and bounded: after a decision, at most every 5 minutes (a failed fetch is
        retried after one), at once when the policy version moved. One caller fetches at a time."""
        try:
            now = self._clock()
            with self._lock:
                table = self._table
                due = now >= self._table_next and (
                    table is None
                    or now - self._table_at >= TABLE_REFRESH_SECONDS
                    or table.policy_version != server_version
                )
                if self._refreshing or not due:
                    return
                self._refreshing = True
                self._table_next = now + TABLE_RETRY_SECONDS
            fetched: FailModeTable | None = None
            try:
                reply = self._call(
                    "GET",
                    "/v1/policies/fail-modes",
                    None,
                    min(TABLE_TIMEOUT, self._c.policy_timeout),
                )
                if 200 <= reply.status < 300:
                    fetched = FailModeTable.parse(reply.body)
            except TransportFailure:
                fetched = None
            finally:
                with self._lock:
                    self._refreshing = False
                    if fetched is not None:
                        self._table, self._table_at = fetched, now
                        self._table_next = now + (
                            5.0 if fetched.policy_version != server_version else 0.0
                        )
        except Exception:
            self._stats.add("internal_errors")
            with self._lock:
                self._refreshing = False


CONSUME_TEXT = "The approval could not be used for this action."


class _WaitBroken(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _safe_name(value: str) -> bool:
    return bool(value) and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None


def _agent_of(run: "Run") -> str:
    span = _current_span.get()
    return span.agent_id if span is not None and span.run is run else run.agent_id


# -- module-level helpers ------------------------------------------------------------------------


def _resolve(run: "Run | None", client: "BlackBox | None") -> "tuple[BlackBox | None, Run | None]":
    target = run if run is not None else _current_run.get()
    if client is None and target is not None:
        client = target._bb
    return client, target


def guard(
    capability: str,
    resource: str | None = None,
    risk_class: str | None = None,
    arguments: dict[str, Any] | None = None,
    *,
    on_error: str | None = None,
    run: "Run | None" = None,
    client: "BlackBox | None" = None,
) -> Decision:
    """`BlackBox.guard` for the current run. With no run and no client there is nothing to
    enforce, so it returns an allow with `via="disabled"`."""
    bb, target = _resolve(run, client)
    if bb is None:
        return DISABLED
    return bb.guard(capability, resource, risk_class, arguments, on_error=on_error, run=target)


async def aguard(
    capability: str,
    resource: str | None = None,
    risk_class: str | None = None,
    arguments: dict[str, Any] | None = None,
    *,
    on_error: str | None = None,
    run: "Run | None" = None,
    client: "BlackBox | None" = None,
) -> Decision:
    bb, target = _resolve(run, client)
    if bb is None:
        return DISABLED
    return await bb.aguard(
        capability, resource, risk_class, arguments, on_error=on_error, run=target
    )


MAX_GUARD_THREADS = 64
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _guard_executor() -> ThreadPoolExecutor:
    """A pool of its own for `aguard`: a pending approval holds a thread for minutes or hours, and
    on the loop's default executor a few of them would starve everything else that uses it
    (`getaddrinfo`, `asyncio.to_thread`)."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(MAX_GUARD_THREADS, thread_name_prefix="blackbox-guard")
        return _executor


def _reset_executor_after_fork() -> None:
    global _executor, _executor_lock
    _executor, _executor_lock = None, threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_executor_after_fork)


async def _run_guard(
    engine: PolicyGuard,
    run: "Run | None",
    capability: Any,
    resource: Any,
    risk_class: Any,
    arguments: Any,
    on_error: Any,
) -> Decision:
    """The blocking guard on a worker thread, cancellable between long-poll rounds."""
    cancel = threading.Event()
    call = functools.partial(
        contextvars.copy_context().run,
        engine.guard,
        run,
        capability,
        resource,
        risk_class,
        arguments,
        on_error,
        cancel,
    )
    try:
        return await asyncio.get_running_loop().run_in_executor(_guard_executor(), call)
    except asyncio.CancelledError:
        cancel.set()  # the worker withdraws a pending approval at its next round (<= 5 s)
        raise


Intent = str | Callable[..., Any] | dict[str, Any] | None


def guarded(
    capability: str,
    resource: Intent = None,
    risk_class: str | None = None,
    arguments: Intent = None,
    *,
    on_error: str | None = None,
    client: "BlackBox | None" = None,
) -> Callable[[F], F]:
    """Guard a function (sync or async, or a method) before it runs.

        @guarded("github.merge", resource=lambda repo, pr: f"{repo}#{pr}", risk_class="R3")
        def merge(repo, pr): ...

    `resource` and `arguments` may be values or callables that receive the wrapped function's own
    call arguments (for a method, `self` first). A callable that raises is `invalid_intent` and
    follows the fail mode. The policy exceptions propagate to the caller and the function does not
    run. This is the integration path for LangGraph, OpenAI and Anthropic tool functions, which
    those adapters do not execute themselves. With no active run (and no `client=`) or with policy
    checks off, the function simply runs.
    """

    def decorate(func: F) -> F:
        def prepare(
            args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> tuple["BlackBox | None", "Run | None", bool, Any, Any]:
            bb, target = _resolve(None, client)
            if bb is None or bb.config.policy_mode != "enforce":
                return None, target, True, None, None
            try:
                res = resource(*args, **kwargs) if callable(resource) else resource
                arg = arguments(*args, **kwargs) if callable(arguments) else arguments
            except Exception:
                return bb, target, False, None, None
            return bb, target, True, res, arg

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                bb, target, ok, res, arg = prepare(args, kwargs)
                if bb is not None:
                    if ok:
                        await bb.aguard(
                            capability, res, risk_class, arg, on_error=on_error, run=target
                        )
                    else:
                        bb._policy_engine().fail(
                            target, capability, risk_class, on_error, REASON_INVALID
                        )
                return await func(*args, **kwargs)

            return cast(F, awrapper)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bb, target, ok, res, arg = prepare(args, kwargs)
            if bb is not None:
                if ok:
                    bb.guard(capability, res, risk_class, arg, on_error=on_error, run=target)
                else:
                    bb._policy_engine().fail(
                        target, capability, risk_class, on_error, REASON_INVALID
                    )
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorate


__all__ = [
    "ApprovalDenied",
    "ApprovalExpired",
    "ApprovalPending",
    "Decision",
    "PolicyDenied",
    "PolicyError",
    "PolicyGuard",
    "PolicyUnavailable",
    "aguard",
    "guard",
    "guarded",
]
