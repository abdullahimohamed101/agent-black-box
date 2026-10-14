"""Decisions and approvals (ADR-141): ask the policy, wait for a person, spend the approval once.

Four callers, one set of rules:

* an agent's key asks (`decide`), waits (`wait`), spends (`consume`) and withdraws (`cancel`) its
  own approvals; it never sees a person's comment, the intent hash or other keys' approvals;
* a signed-in person lists, reads and decides (`list_approvals`, `detail`, `decide_as_person`);
* the expiry job and lazy sweeps close approvals whose time has passed.

Every transition is one atomic statement in the store; the event and the audit row of the
transition are written in the same transaction by the caller that won it. Nothing here logs or
returns the intent hash, the resource, the arguments or a comment.
"""

import asyncio
import base64
import hmac
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.approvals import model as m
from abb_api.approvals import schemas as s
from abb_api.approvals.events import RunRef, decision_event, derived_event_id, transition_event
from abb_api.approvals.intent import IntentIn, attributes_of, hash_of, run_uuid
from abb_api.approvals.lifecycle import (
    emit,
    expire_one,
    record,
    run_ref,
    run_rejected,
    sweep_expired,
)
from abb_api.approvals.model import Approval, NewApproval
from abb_api.approvals.roles import may_decide
from abb_api.approvals.serverkey import event_id_key
from abb_api.approvals.store import PgApprovalStore
from abb_api.approvals.waiters import WaiterGate
from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.auth.repository import ApiKeyLookup
from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.clock import Clock
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, retry_after_seconds
from abb_api.ingestion.service import project_key_required
from abb_api.jobs.outbox import EXPIRE_APPROVAL, OutboxRepository
from abb_api.policies.evaluator import evaluate
from abb_api.policies.model import (
    DEFAULT_EXPIRES_SECONDS,
    EMPTY_POLICY,
    Decision,
    DecisionKind,
    Intent,
    Rule,
)
from abb_api.policies.parser import compile_cached
from abb_api.policies.store import PgPolicyStore
from abb_api.projects.access import authorise_project
from abb_api.security.redaction import ServerRedactor
from abb_api.streaming.hub import StreamHub
from abb_api.tenancy import TenantContext
from abb_api.workspaces.repository import MembershipRepository

logger = logging.getLogger(__name__)

MAX_LIST_PAGE = 100
MAX_SCRUBBED_JSON_BYTES = 60_000
MIN_POLL_GAP_SECONDS = 0.25
MISMATCH_AUDIT_SECONDS = 60.0
MISMATCH_AUDIT_TRACKED = 10_000
REASON_FALLBACK = "The policy author's reason is not available."
CLOSED_CLASSES = frozenset({"R3", "R4"})

_ERRORS_BY_STATE = {
    m.DENIED: m.NOT_APPROVED,
    m.CANCELLED: m.NOT_APPROVED,
    m.EXPIRED: m.EXPIRED_CODE,
}


def default_fail_mode(risk_class: str | None) -> str:
    """R3 and R4 fail closed; R0-R2 and unclassified actions fail open (D3)."""
    return "closed" if risk_class in CLOSED_CLASSES else "open"


def approval_error(code: str, status: int, message: str, **details: Any) -> AppError:
    return AppError(
        code,
        message,
        category=ErrorCategory.NOT_FOUND if status == 404 else ErrorCategory.CONFLICT,
        status_code=status,
        details=dict(details),
    )


def not_found() -> AppError:
    return approval_error(m.NOT_FOUND, 404, "Approval not found.")


def policy_unavailable() -> AppError:
    return AppError(
        "POLICY_UNAVAILABLE",
        "The governing policy cannot be read right now.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
        headers={"Retry-After": "2"},
    )


def _rate_limited(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "This key is asking the policy service faster than its limit.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def _bucket(per_minute: int) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        events_per_second=per_minute / 60,
        burst_events=per_minute,
        bytes_per_second=1.0,
        burst_bytes=1,
    )


def _json_size(value: Any) -> int:
    import json

    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def encode_cursor(approval: Approval) -> str:
    raw = f"{approval.requested_at.isoformat()}|{approval.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError):
        return None


class ApprovalService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        clock: Clock,
        hub: StreamHub | None = None,
        redactor: ServerRedactor | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._clock = clock
        self._hub = hub
        self._redactor = redactor or ServerRedactor(
            event_max_seconds=settings.security_scan_event_max_seconds
        )
        self._summary_delay = timedelta(seconds=settings.summary_debounce_seconds)
        self._key_limiter = _bucket(settings.policy_decide_per_minute_key)
        self._workspace_limiter = _bucket(settings.policy_decide_per_minute_workspace)
        self._mismatch_audited: dict[uuid.UUID, float] = {}
        self.waiters = WaiterGate(
            settings.approval_waiters_per_workspace, settings.approval_waiters_per_process
        )

    # ------------------------------------------------------------------ shared pieces

    def _agent(self, principal: Principal) -> tuple[uuid.UUID, str]:
        """The project and key id of an agent's key; a workspace-wide key cannot act here."""
        if principal.project_id is None:
            raise project_key_required()
        wait = self._key_limiter.acquire(principal.actor_id, events=1, bytes_=0)
        if wait is None:
            wait = self._workspace_limiter.acquire(str(principal.workspace_id), events=1, bytes_=0)
        if wait is not None:
            raise _rate_limited(wait)
        return principal.project_id, principal.actor_id.removeprefix("key:")

    @staticmethod
    def _parse(approval_id: str) -> uuid.UUID:
        parsed = parse_public_id(IdKind.APPROVAL, approval_id)
        if parsed is None:
            raise not_found()
        return parsed

    def _emit(self, conn: AsyncConnection, tenant: TenantContext, events: list[Any]) -> Any:
        return emit(conn, tenant, events, self._summary_delay)

    async def _sweep_one(self, tenant: TenantContext, approval_id: uuid.UUID) -> None:
        """Close this approval if its time has passed (committed on its own, so the answer the
        caller is about to give is already true)."""
        async with self._engine.begin() as conn:
            await expire_one(
                conn, tenant, approval_id, self._clock(), summary_delay=self._summary_delay
            )

    async def _sweep_workspace(self, tenant: TenantContext) -> None:
        async with self._engine.begin() as conn:
            await sweep_expired(conn, tenant, self._clock(), summary_delay=self._summary_delay)

    @staticmethod
    async def _governing(
        conn: AsyncConnection, tenant: TenantContext, project_id: uuid.UUID
    ) -> Any:
        """The compiled policy that governs a project now (the empty policy if none)."""
        found = await PgPolicyStore(conn, tenant).for_decision(project_id)
        if found is None:
            return EMPTY_POLICY
        compiled = compile_cached(found[1])
        if compiled is None:
            raise policy_unavailable()
        return compiled.with_version(found[0])

    def _scrub_text(self, value: str) -> str | None:
        scrubbed = self._redactor.scrub_json(value)
        return scrubbed.value if scrubbed and isinstance(scrubbed.value, str) else None

    # ------------------------------------------------------------------ the decision

    async def decide(self, principal: Principal, body: IntentIn) -> s.DecideOut:
        project_id, key_id = self._agent(principal)
        digest = hash_of(principal, body)
        run_id = run_uuid(body)
        tenant = principal.tenant
        now = self._clock()
        key = await event_id_key(self._engine)

        async with self._engine.begin() as conn:
            store = PgApprovalStore(conn, tenant)
            traced = await store.run_trace(run_id)
            if traced is not None and traced[0] != project_id:
                raise run_rejected()  # a run of another project: the same 404 as no run
            if traced is None and not holds(principal, actions.EVENT_WRITE):
                # A key that may only ask (`policy:check`) cannot create runs by asking about
                # ones that do not exist (review P3-4); a key that may ingest can anyway.
                raise run_rejected()
            trace_id = traced[1] if traced else self._trace_for(body, run_id)
            ref = RunRef(project_id, run_id, trace_id, body.agent_id)

            policy = await self._governing(conn, tenant, project_id)
            decision = evaluate(
                policy,
                Intent(body.capability, body.resource, body.risk_class, attributes_of(body)),
            )
            rule = next((r for r in policy.rules if r.id == decision.rule_id), None)
            on_error = (rule.on_error.value if rule and rule.on_error else None) or (
                default_fail_mode(body.risk_class)
            )
            reason = self._scrub_text(decision.reason) or REASON_FALLBACK
            out = s.DecideOut(
                decision=decision.decision.value,
                policy_id=decision.rule_id,
                policy_version=decision.policy_version,
                reason=reason,
                on_error=on_error,  # type: ignore[arg-type]
                approval_id=None,
                approval_state=None,
                expires_at=None,
                on_expiry=None,
            )

            if decision.decision is DecisionKind.REQUIRE_APPROVAL and rule is not None:
                approval, created = await self._request(
                    conn, tenant, store, body, ref, key_id, digest, rule, policy.version, now
                )
                if created:
                    await self._emit(
                        conn,
                        tenant,
                        [
                            decision_event(
                                tenant,
                                ref,
                                kind="require_approval",
                                rule_id=rule.id,
                                policy_version=policy.version,
                                capability=body.capability,
                                reason=reason,
                                now=now,
                                approval_id=approval.id,
                                event_id=derived_event_id(
                                    key,
                                    tenant.workspace_id,
                                    "approval",
                                    public_id(IdKind.APPROVAL, approval.id),
                                    "decision",
                                ),
                            ),
                            # One microsecond later, so the request sorts after its decision.
                            transition_event(
                                tenant, ref, approval, now=now + timedelta(microseconds=1), key=key
                            ),
                        ],
                    )
                return out.model_copy(
                    update={
                        "approval_id": public_id(IdKind.APPROVAL, approval.id),
                        "approval_state": approval.state,
                        "expires_at": approval.expires_at,
                        "on_expiry": approval.on_expiry,
                    }
                )

            # allow (or a default deny): an event, no approval row, no audit row.
            event_id = (
                # The client's key alone never names the event: the server secret and the intent
                # are mixed in, so another intent under the same key is its own event.
                derived_event_id(
                    key, tenant.workspace_id, "decide", body.run_id, body.idempotency_key, digest
                )
                if body.idempotency_key
                else None
            )
            await self._emit(
                conn,
                tenant,
                [
                    decision_event(
                        tenant, ref, kind=decision.decision.value, rule_id=decision.rule_id,
                        policy_version=decision.policy_version, capability=body.capability,
                        reason=reason, now=now, event_id=event_id,
                    )
                ],
            )  # fmt: skip
            return out

    @staticmethod
    def _trace_for(body: IntentIn, run_id: uuid.UUID) -> uuid.UUID:
        """The trace of a run that has no events yet: the SDK's hint, else derived from the run."""
        hinted = parse_public_id(IdKind.TRACE, body.trace_id) if body.trace_id else None
        return hinted or run_id

    async def _request(
        self,
        conn: AsyncConnection,
        tenant: TenantContext,
        store: PgApprovalStore,
        body: IntentIn,
        ref: RunRef,
        key_id: str,
        digest: str,
        rule: Rule,
        policy_version: int,
        now: datetime,
    ) -> tuple[Approval, bool]:
        """The open approval for this intent, or a new one: `(approval, created)`."""
        await store.lock_intent(ref.run_id, key_id, digest)
        await sweep_expired(
            conn, tenant, now, summary_delay=self._summary_delay, run_id=ref.run_id, limit=20
        )
        existing = await store.find_open(ref.run_id, key_id, digest, now)
        if existing is not None:
            return existing, False
        # A runaway or hostile agent must not bury the approvers: bounded open requests per key
        # and per workspace (the SDK treats the refusal as an unavailable service: its fail mode).
        await store.lock_caps()  # count and insert are one step per workspace
        per_key, per_workspace = await store.open_counts(key_id, now)
        if (
            per_key >= self._settings.approval_max_open_per_key
            or per_workspace >= self._settings.approval_max_open_per_workspace
        ):
            raise AppError(
                "TOO_MANY_OPEN_APPROVALS",
                "Too many approval requests are waiting; decide or withdraw some first.",
                category=ErrorCategory.RATE_LIMIT,
                status_code=429,
                retryable=True,
                details={"retry_after_seconds": 30},
                headers={"Retry-After": "30"},
            )

        key = await ApiKeyLookup(conn).find(key_id)
        resource: str | None = ""
        redactions = 0
        if body.resource:
            cleaned = self._redactor.scrub_json(body.resource)
            resource = (
                cleaned.value if cleaned and isinstance(cleaned.value, str) else None
            )  # None: withheld
            redactions += cleaned.count if cleaned else 0
        scrubbed = self._redactor.scrub_json(body.arguments)
        arguments = scrubbed.value if scrubbed is not None else None
        if arguments is not None and _json_size(arguments) > MAX_SCRUBBED_JSON_BYTES:
            arguments = None  # redaction markers can outgrow what they replace: withhold
        redactions += scrubbed.count if scrubbed else 0
        new = NewApproval(
            id=new_uuid(IdKind.APPROVAL),
            project_id=ref.project_id,
            run_id=ref.run_id,
            agent_id=body.agent_id,
            capability=body.capability,
            resource_scrubbed=resource[:4096] if resource is not None else None,
            risk_class=body.risk_class,
            arguments_scrubbed=arguments,
            scrub_dropped=resource is None or arguments is None,
            scrub_count=redactions,
            intent_hash=digest,
            policy_rule_id=rule.id,
            policy_version=policy_version,
            approver_role=rule.approver_role,
            allow_self_approval=rule.allow_self_approval,
            on_expiry=(rule.on_expiry.value if rule.on_expiry else "deny"),
            requested_at=now,
            expires_at=now + timedelta(seconds=rule.expires_in_seconds or DEFAULT_EXPIRES_SECONDS),
            requested_by_key_id=key_id,
            requested_by_user_id=key.created_by if key else None,
        )
        approval = await store.insert(new)
        # The timer that closes it: a job due at `expires_at` (lazy sweeps cover the gap).
        await OutboxRepository(conn, tenant).enqueue(
            job_type=EXPIRE_APPROVAL,
            dedupe_key=str(approval.id),
            payload={"approval_id": str(approval.id)},
            delay=approval.expires_at - now,
        )
        return approval, True

    # ------------------------------------------------------------------ waiting

    async def _view(
        self, principal: Principal, project_id: uuid.UUID, key_id: str, approval_id: uuid.UUID
    ) -> Approval:
        """The caller's own approval as it is now (closed first if its time has passed)."""
        async with self._engine.connect() as conn:
            approval = await PgApprovalStore(conn, principal.tenant).get(
                approval_id, project_id=project_id, key_id=key_id
            )
        if approval is None:
            raise not_found()
        if approval.is_open and approval.expires_at <= self._clock():
            await self._sweep_one(principal.tenant, approval_id)
            async with self._engine.connect() as conn:
                approval = (
                    await PgApprovalStore(conn, principal.tenant).get(
                        approval_id, project_id=project_id, key_id=key_id
                    )
                    or approval
                )
        return approval

    @staticmethod
    def _wait_out(approval: Approval) -> s.WaitOut:
        return s.WaitOut(
            approval_id=public_id(IdKind.APPROVAL, approval.id),
            state=approval.state,  # type: ignore[arg-type]
            expires_at=approval.expires_at,
            on_expiry=approval.on_expiry,  # type: ignore[arg-type]
            policy_id=approval.policy_rule_id,
            policy_version=approval.policy_version,
            consumed=approval.consumed_at is not None,
        )

    async def wait(self, principal: Principal, approval_id: str, seconds: float) -> s.WaitOut:
        """Long poll: the approval's state now if it is no longer REQUESTED, else the state at the
        first change or after `seconds`. No database connection is held between reads."""
        project_id, key_id = self._agent(principal)
        identifier = self._parse(approval_id)
        approval = await self._view(principal, project_id, key_id, identifier)
        if approval.state != m.REQUESTED or seconds <= 0:
            return self._wait_out(approval)
        subscription = None
        entered = False
        try:
            # Everything that can fail is inside the try, so the slot is always given back
            # (review P3-3: a failing subscribe used to leak one for the process lifetime).
            self.waiters.enter(principal.workspace_id)
            entered = True
            if self._hub:
                subscription = self._hub.subscribe((principal.workspace_id, approval.run_id))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            poll = self._settings.approval_wait_poll_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return self._wait_out(approval)
                pause = min(remaining, poll)
                started = loop.time()
                if subscription is not None:
                    await subscription.wait(pause)
                else:
                    await asyncio.sleep(pause)
                approval = await self._view(principal, project_id, key_id, identifier)
                if approval.state != m.REQUESTED:
                    return self._wait_out(approval)
                gap = MIN_POLL_GAP_SECONDS - (loop.time() - started)
                if gap > 0 and deadline - loop.time() > gap:
                    await asyncio.sleep(gap)  # a chatty run must not turn a wake into a spin
        finally:
            if subscription is not None:
                subscription.close()
            if entered:
                self.waiters.leave(principal.workspace_id)

    # ------------------------------------------------------------------ spending

    async def consume(
        self, principal: Principal, approval_id: str, body: s.ConsumeIn
    ) -> s.ConsumeOut:
        """Spend the approval for exactly this intent. The server recomputes the hash from the
        presented intent; every refusal is one of five fixed codes and echoes nothing."""
        try:
            return await self._consume(principal, approval_id, body)
        except AppError as error:
            if error.code == m.INTENT_MISMATCH:
                await self._audit_mismatch(principal, approval_id)
            raise

    async def _audit_mismatch(self, principal: Principal, approval_id: str) -> None:
        """A different action presented under an approval is the replay attempt this phase exists
        to catch: leave an audit row (reason code only), at most one per approval per minute so a
        retry loop cannot flood the log (review P3-8). Best effort: never changes the answer."""
        identifier = self._parse(approval_id)
        now = self._clock().timestamp()
        last = self._mismatch_audited.get(identifier)
        if last is not None and now - last < MISMATCH_AUDIT_SECONDS:
            return
        if len(self._mismatch_audited) >= MISMATCH_AUDIT_TRACKED:
            self._mismatch_audited.clear()  # bounded: forgetting only allows one extra row
        self._mismatch_audited[identifier] = now
        try:
            async with self._engine.begin() as conn:
                await AuditRepository(conn, principal.tenant).append(
                    AuditEntry(
                        actor_kind="api_key",
                        actor_id=principal.actor_id,
                        action="approval.consume_refused",
                        resource_kind="approval",
                        resource_id=approval_id,
                        details={"approval_id": approval_id, "reason": m.INTENT_MISMATCH},
                    )
                )
        except Exception:
            logger.warning("approval audit failed", extra={"reason": "consume_refused"})

    async def _consume(
        self, principal: Principal, approval_id: str, body: s.ConsumeIn
    ) -> s.ConsumeOut:
        project_id, key_id = self._agent(principal)
        identifier = self._parse(approval_id)
        digest = hash_of(principal, body.intent)
        run_id = run_uuid(body.intent)
        tenant = principal.tenant
        await self._sweep_one(tenant, identifier)
        now = self._clock()
        refusal: AppError | None = None
        async with self._engine.begin() as conn:
            store = PgApprovalStore(conn, tenant)
            approval = await store.get(identifier, project_id=project_id, key_id=key_id)
            if approval is None:
                raise not_found()
            spent: Approval | None = None
            current: tuple[str | None, int] | None = None
            if (
                approval.state == m.APPROVED
                and approval.consumed_at is None
                and (approval.expires_at > now)
            ):
                if approval.run_id != run_id or not hmac.compare_digest(
                    approval.intent_hash, digest
                ):
                    raise self._rejected(
                        m.INTENT_MISMATCH, "The approval is for a different action."
                    )
                # A grant is not a licence to ignore a policy published since: re-read what
                # governs the project now, holding off publishers until this transaction ends.
                await PgPolicyStore(conn, tenant).lock_governing_shared(project_id)
                policy = await self._governing(conn, tenant, project_id)
                decision = evaluate(
                    policy,
                    Intent(
                        body.intent.capability,
                        body.intent.resource,
                        body.intent.risk_class,
                        attributes_of(body.intent),
                    ),
                )
                current = (decision.rule_id, decision.policy_version)
                if decision.decision is DecisionKind.DENY:
                    # Committed (event and audit row), then refused; the approval is unchanged.
                    await self._record_policy_refusal(
                        conn, tenant, store, approval, decision, principal, now
                    )
                    refusal = self._rejected(
                        m.POLICY_DENIED, "The current policy denies this action."
                    )
                else:
                    spent = await store.consume(
                        identifier,
                        project_id=project_id,
                        key_id=key_id,
                        intent_hash=digest,
                        run_id=run_id,
                        now=now,
                    )
            if refusal is None:
                if spent is None:
                    # Not consumable, or another caller spent it first: say why, from a fresh
                    # read.
                    cur = await store.get(identifier, project_id=project_id, key_id=key_id)
                    raise self._consume_refusal(cur or approval, now)
                await record(
                    conn, tenant, spent, now=now, summary_delay=self._summary_delay,
                    audit_action="approval.consume", actor_kind="api_key",
                    actor_id=principal.actor_id, consumed=True, current=current,
                    audit_details={
                        "current_policy_rule": (current[0] if current else None) or "default",
                        "current_policy_version": current[1] if current else None,
                    },
                )  # fmt: skip
        if refusal is not None:
            raise refusal
        return s.ConsumeOut(approval_id=approval_id, consumed_at=now)

    async def _record_policy_refusal(
        self,
        conn: AsyncConnection,
        tenant: TenantContext,
        store: PgApprovalStore,
        approval: Approval,
        decision: Decision,
        principal: Principal,
        now: datetime,
    ) -> None:
        """A granted approval was refused because the policy now denies the action: one
        `policy.action.blocked` event per (approval, policy version), and an audit row."""
        ref = await run_ref(store, approval)
        identifier = public_id(IdKind.APPROVAL, approval.id)
        reason = self._scrub_text(decision.reason) or REASON_FALLBACK
        await self._emit(
            conn,
            tenant,
            [
                decision_event(
                    tenant, ref, kind="deny", rule_id=decision.rule_id,
                    policy_version=decision.policy_version, capability=approval.capability,
                    reason=reason, now=now, approval_id=approval.id,
                    event_id=derived_event_id(
                        await event_id_key(self._engine),
                        tenant.workspace_id, "approval", identifier, "policy-denied",
                        str(decision.policy_version),
                    ),
                )
            ],
        )  # fmt: skip
        await AuditRepository(conn, tenant).append(
            AuditEntry(
                actor_kind="api_key",
                actor_id=principal.actor_id,
                action="approval.consume_refused",
                resource_kind="approval",
                resource_id=identifier,
                details={
                    "approval_id": identifier,
                    "reason": m.POLICY_DENIED,
                    "policy_rule": decision.rule_id or "default",
                    "policy_version": decision.policy_version,
                },
            )
        )

    @staticmethod
    def _rejected(code: str, message: str) -> AppError:
        logger.warning("approval consume rejected", extra={"reason": code})
        return approval_error(code, 409, message)

    def _consume_refusal(self, approval: Approval, now: datetime) -> AppError:
        if approval.state == m.APPROVED:
            if approval.consumed_at is not None:
                return self._rejected(m.CONSUMED_CODE, "The approval has already been used.")
            if approval.expires_at <= now:
                return self._rejected(m.EXPIRED_CODE, "The approval has expired.")
            return self._rejected(m.INTENT_MISMATCH, "The approval is for a different action.")
        if approval.state == m.REQUESTED and approval.expires_at <= now:
            return self._rejected(m.EXPIRED_CODE, "The approval has expired.")
        code = _ERRORS_BY_STATE.get(approval.state, m.NOT_APPROVED)
        text = (
            "The approval has expired."
            if code == m.EXPIRED_CODE
            else "The approval is not granted."
        )
        return self._rejected(code, text)

    async def cancel(self, principal: Principal, approval_id: str) -> None:
        project_id, key_id = self._agent(principal)
        identifier = self._parse(approval_id)
        tenant = principal.tenant
        await self._sweep_one(tenant, identifier)
        now = self._clock()
        async with self._engine.begin() as conn:
            store = PgApprovalStore(conn, tenant)
            cancelled = await store.cancel(
                identifier, project_id=project_id, key_id=key_id, now=now
            )
            if cancelled is None:
                current = await store.get(identifier, project_id=project_id, key_id=key_id)
                if current is None:
                    raise not_found()
                raise approval_error(
                    m.NOT_REQUESTED, 409, "The approval is no longer waiting.", state=current.state
                )
            await record(
                conn, tenant, cancelled, now=now, summary_delay=self._summary_delay,
                audit_action="approval.cancel", actor_kind="api_key", actor_id=principal.actor_id,
            )  # fmt: skip

    # ------------------------------------------------------------------ people

    def _summary(self, approval: Approval) -> s.ApprovalSummary:
        now = self._clock()
        usable = approval.is_open and approval.expires_at > now
        return s.ApprovalSummary(
            id=public_id(IdKind.APPROVAL, approval.id),
            state=approval.state,  # type: ignore[arg-type]
            project_id=public_id(IdKind.PROJECT, approval.project_id),
            run_id=public_id(IdKind.RUN, approval.run_id),
            agent_id=approval.agent_id,
            capability=approval.capability,
            resource=approval.resource_scrubbed,
            risk_class=approval.risk_class,
            policy_id=approval.policy_rule_id,
            policy_version=approval.policy_version,
            approver_role=approval.approver_role,  # type: ignore[arg-type]
            requested_at=approval.requested_at,
            expires_at=approval.expires_at,
            seconds_left=int((approval.expires_at - now).total_seconds()) if usable else None,
            decided_at=approval.decided_at,
            decided_by=(
                public_id(IdKind.USER, approval.decided_by_user_id)
                if approval.decided_by_user_id
                else None
            ),
            consumed=approval.consumed_at is not None,
        )

    def _detail(self, approval: Approval) -> s.ApprovalDetail:
        return s.ApprovalDetail(
            **self._summary(approval).model_dump(),
            arguments=approval.arguments_scrubbed,
            content_withheld=approval.scrub_dropped,
            redactions=approval.scrub_count,
            allow_self_approval=approval.allow_self_approval,
            on_expiry=approval.on_expiry,  # type: ignore[arg-type]
            comment=approval.decision_comment,
            closed_at=approval.closed_at,
            consumed_at=approval.consumed_at,
        )

    async def list_approvals(
        self,
        principal: Principal,
        *,
        state: str,
        project_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> s.ApprovalList:
        await self._sweep_workspace(principal.tenant)
        async with self._engine.connect() as conn:
            scope = await authorise_project(conn, principal, project_id)
        before = None
        if cursor is not None:
            before = decode_cursor(cursor)
            if before is None:
                raise AppError(
                    "CURSOR_INVALID",
                    "The cursor is not valid.",
                    category=ErrorCategory.VALIDATION,
                    status_code=400,
                )
        async with self._engine.connect() as conn:
            rows = await PgApprovalStore(conn, principal.tenant).listing(
                pending_at=self._clock() if state == "pending" else None,
                decided=state == "decided",
                project_id=scope,
                limit=limit + 1,
                before=before,
            )
        page = rows[:limit]
        return s.ApprovalList(
            items=[self._summary(a) for a in page],
            next_cursor=encode_cursor(page[-1]) if len(rows) > limit else None,
        )

    async def pending_count(self, principal: Principal) -> s.PendingCount:
        await self._sweep_workspace(principal.tenant)
        async with self._engine.connect() as conn:
            count = await PgApprovalStore(conn, principal.tenant).pending_count(self._clock(), None)
        return s.PendingCount(count=count)

    async def detail(self, principal: Principal, approval_id: str) -> s.ApprovalDetail:
        identifier = self._parse(approval_id)
        await self._sweep_one(principal.tenant, identifier)
        async with self._engine.connect() as conn:
            approval = await PgApprovalStore(conn, principal.tenant).get(identifier)
        if approval is None:
            raise not_found()
        return self._detail(approval)

    async def decide_as_person(
        self, principal: Principal, approval_id: str, body: s.DecisionIn
    ) -> s.ApprovalDetail:
        """Approve or deny. Who may: the member's current role must satisfy the rule's
        `approver_role`, they must hold `payload.read`, and they may not decide a request their own
        key made unless the rule says so. One atomic statement picks the single winner of any
        race; the winner's event and audit row commit with it."""
        if principal.user_id is None:  # sessions only; no key holds `approval.decide`
            raise AppError(
                "APPROVER_ROLE_REQUIRED",
                "Only a signed-in person may decide an approval.",
                category=ErrorCategory.AUTHORIZATION,
                status_code=403,
            )
        identifier = self._parse(approval_id)
        tenant = principal.tenant
        await self._sweep_one(tenant, identifier)
        now = self._clock()
        target = m.APPROVED if body.decision == "approve" else m.DENIED
        comment: str | None = None
        if body.comment:
            comment = self._scrub_text(body.comment)  # None: withheld, never stored raw
        async with self._engine.begin() as conn:
            store = PgApprovalStore(conn, tenant)
            approval = await store.get(identifier)
            if approval is None:
                raise not_found()
            role = await MembershipRepository(conn, tenant).role_of(principal.user_id)
            if not holds(principal, actions.PAYLOAD_READ) or not may_decide(
                approval.approver_role, role
            ):
                raise AppError(
                    "APPROVER_ROLE_REQUIRED",
                    "Your role may not decide this approval.",
                    category=ErrorCategory.AUTHORIZATION,
                    status_code=403,
                )
            if target == m.APPROVED and approval.scrub_dropped:
                # The scanner withheld part of what is being approved (too large, out of time,
                # a scan error): an approver must never approve what they cannot inspect.
                # Denying stays possible. Fails closed (review P2-4).
                raise approval_error(
                    m.CONTENT_UNAVAILABLE,
                    409,
                    "The content of this request could not be shown, so it cannot be approved.",
                )
            if (
                approval.requested_by_user_id == principal.user_id
                and not approval.allow_self_approval
            ):
                raise AppError(
                    "SELF_APPROVAL_FORBIDDEN",
                    "You cannot decide a request made with your own API key.",
                    category=ErrorCategory.AUTHORIZATION,
                    status_code=403,
                )
            decided = await store.decide(
                identifier, state=target, user_id=principal.user_id, comment=comment, now=now
            )
            if decided is None:
                current = await store.get(identifier) or approval
                raise approval_error(
                    m.NOT_REQUESTED, 409, "The approval is no longer waiting.", state=current.state
                )
            await record(
                conn, tenant, decided, now=now, summary_delay=self._summary_delay,
                audit_action="approval.decide", actor_kind="user", actor_id=principal.actor_id,
                audit_details={
                    "decision": body.decision,
                    "approver_role": role or "",
                    "policy_id": decided.policy_rule_id,
                },
            )  # fmt: skip
        return self._detail(decided)
