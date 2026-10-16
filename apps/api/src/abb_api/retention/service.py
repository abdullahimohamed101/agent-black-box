"""Use cases behind the retention routes: policy, preview and reports (ADR-159).

The service reads and writes the policy and reads reports; it can neither start the retention job
nor delete anything. The preview measures what the *current or a proposed* policy would delete,
within a statement timeout and the per-tier bounds, without saving or changing anything.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from abb_event_schema.ids import IdKind
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.authz import actions
from abb_api.authz.audit import authorize_audited
from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.config import RETENTION_FLOOR_DAYS, Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id, public_id
from abb_api.ingestion.ratelimit import retry_after_seconds
from abb_api.limits import Scope, SharedCounters
from abb_api.limits.factory import minute_limiter
from abb_api.retention import model as m
from abb_api.retention import schemas as s
from abb_api.retention.measure import Limits, measure_tier
from abb_api.retention.repository import PolicyRepository
from abb_api.security.settings import Actor

AUDIT_ACTION = "retention.policy.update"


def workspace_not_found() -> AppError:
    return AppError(
        "WORKSPACE_NOT_FOUND",
        "Workspace not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def policy_invalid(error: m.RetentionInvalid, audit_floor: int = RETENTION_FLOOR_DAYS) -> AppError:
    floor = m.floor_of(error.tier, audit_floor)
    messages = {
        "below_floor": f"Retention must be at least {floor} days (or forever).",
        "above_ceiling": "Retention is longer than the permitted maximum.",
        "order": (
            "Artifacts cannot be kept longer than trace detail, nor trace detail longer than the "
            "run record: the run record is deleted last."
        ),
    }
    return AppError(
        "RETENTION_POLICY_INVALID",
        messages[error.reason],
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"tier": error.tier.value, "reason": error.reason},
    )


def preview_timeout() -> AppError:
    return AppError(
        "RETENTION_PREVIEW_TIMEOUT",
        "The preview took too long; try again later or a shorter retention period.",
        category=ErrorCategory.TIMEOUT,
        status_code=504,
        retryable=True,
    )


def _rate_limited(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "Retention requests are limited per workspace; try again shortly.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def _audit_value(setting: m.TierSetting) -> Any:
    return "default" if setting is None else setting


class RetentionApiService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        clock: Clock,
        counters: SharedCounters | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._clock = clock
        self._defaults = m.defaults_of(settings)
        divisor = settings.limits_block_divisor
        self._write_limiter = minute_limiter(
            counters, Scope.RETENTION_WRITE, settings.retention_writes_per_minute,
            block_divisor=divisor,
        )  # fmt: skip
        self._preview_limiter = minute_limiter(
            counters, Scope.RETENTION_PREVIEW, settings.retention_previews_per_minute,
            block_divisor=divisor,
        )  # fmt: skip

    @staticmethod
    def _own_workspace(principal: Principal, raw: str) -> None:
        """Another workspace's id reads exactly like an unknown one (404, never 403)."""
        parsed = parse_public_id(IdKind.WORKSPACE, raw)
        if parsed is None or parsed != principal.workspace_id:
            raise workspace_not_found()

    async def _throttle(self, principal: Principal, *, preview: bool) -> None:
        limiter = self._preview_limiter if preview else self._write_limiter
        wait = await limiter.acquire(
            str(principal.workspace_id), workspace_id=principal.workspace_id
        )
        if wait is not None:
            raise _rate_limited(wait)

    @property
    def _audit_floor(self) -> int:
        return self._settings.retention_audit_floor_days

    def _validate(
        self, merged: dict[m.Tier, m.TierSetting], changed: list[m.Tier]
    ) -> dict[m.Tier, int | None]:
        """The saved choices must be a valid policy. The audit minimum applies to a value being
        changed, not to one saved before the minimum existed (the job enforces it, KI-254), so such
        a workspace can still edit its other tiers."""
        try:
            days = m.validate(self._defaults, merged)
            if m.Tier.AUDIT in changed:
                m.check_setting(m.Tier.AUDIT, merged[m.Tier.AUDIT], self._audit_floor)
        except m.RetentionInvalid as error:
            raise policy_invalid(error, self._audit_floor) from None
        return days

    def _grace(self) -> timedelta:
        return timedelta(hours=self._settings.retention_grace_hours)

    def _view(self, policy: Any, tier: m.Tier, now: datetime) -> s.TierPolicyOut:
        setting = policy.settings[tier]
        days = m.days_of(self._defaults, tier, setting)
        waiting = m.active(policy.pending.get(tier), now)
        pending = None
        if waiting is not None:
            current = m.enforced_days(
                self._defaults, tier, setting, waiting, now, self._audit_floor
            )
            pending = s.PendingShorteningOut(
                effective_at=waiting.effective_at,
                current_days=current,
                current_forever=current is None,
            )
        return s.TierPolicyOut(
            tier=tier.value,
            days=days,
            forever=days is None,
            pending=pending,
            source="default" if setting is None else "workspace",
            default_days=self._defaults[tier],
        )

    def _policy_out(self, policy: Any) -> s.RetentionPolicyOut:
        now = self._clock().astimezone(UTC)
        return s.RetentionPolicyOut(
            tiers=[self._view(policy, tier, now) for tier in m.TIERS],
            mode=self._settings.retention_mode,
            floor_days=RETENTION_FLOOR_DAYS,
            audit_floor_days=self._audit_floor,
            grace_hours=self._settings.retention_grace_hours,
            updated_at=policy.updated_at,
            updated_by=policy.updated_by,
        )

    async def get(self, principal: Principal, workspace: str) -> s.RetentionPolicyOut:
        self._own_workspace(principal, workspace)
        async with self._engine.connect() as conn:
            return self._policy_out(await PolicyRepository(conn, principal.tenant).get())

    async def update(
        self, principal: Principal, workspace: str, body: s.RetentionPolicyIn, request: Request
    ) -> s.RetentionPolicyOut:
        self._own_workspace(principal, workspace)
        await self._throttle(principal, preview=False)
        given = {
            tier: getattr(body, tier.value)
            for tier in m.Tier
            if tier.value in body.model_fields_set
        }
        actor = Actor.of(principal)
        now = self._clock().astimezone(UTC)
        async with self._engine.begin() as conn:
            repo = PolicyRepository(conn, principal.tenant)
            await repo.ensure_row(actor.id[:128])
            before = await repo.get(lock=True)
            merged = {**before.settings, **given}
            changed = [tier for tier in m.Tier if before.settings[tier] != merged[tier]]
            self._validate(merged, changed)
            if m.Tier.AUDIT in changed:
                # The audit log shows who changed the policy: only an owner may change its period.
                await authorize_audited(request, principal, actions.RETENTION_AUDIT_WRITE)
            pending = {
                tier: waiting
                for tier in m.Tier
                if (
                    waiting := m.plan_pending(
                        self._defaults, tier, before.settings[tier], before.pending.get(tier),
                        merged[tier], now, self._grace(), self._audit_floor,
                    )
                )
                is not None
            }  # fmt: skip
            if changed or before.pending.keys() != pending.keys():
                await repo.write(merged, pending, actor.id[:128])
                audit = AuditRepository(conn, principal.tenant)
                for tier in changed:
                    waiting = pending.get(tier)
                    # The tier, the two values and when it takes effect: the whole of what changed.
                    await audit.append(
                        AuditEntry(
                            actor_kind=actor.kind,
                            actor_id=actor.id,
                            action=AUDIT_ACTION,
                            resource_kind="workspace",
                            resource_id=public_id(IdKind.WORKSPACE, principal.workspace_id),
                            details={
                                "tier": tier.value,
                                "old": _audit_value(before.settings[tier]),
                                "new": _audit_value(merged[tier]),
                                "effective_at": (
                                    waiting.effective_at if waiting else now
                                ).isoformat(),
                            },
                        )
                    )
            return self._policy_out(await repo.get())

    async def preview(
        self,
        principal: Principal,
        workspace: str,
        tiers: list[m.Tier],
        proposed: dict[m.Tier, m.TierSetting],
    ) -> s.RetentionPreviewOut:
        self._own_workspace(principal, workspace)
        await self._throttle(principal, preview=True)
        now = self._clock().astimezone(UTC)
        limits = Limits(
            self._settings.retention_preview_max_runs, self._settings.retention_max_rows_per_tick
        )
        timeout_ms = max(int(self._settings.retention_preview_timeout_seconds * 1000), 1)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                policy = await PolicyRepository(conn, principal.tenant).get()
                merged = {**policy.settings, **proposed}
                days = self._validate(
                    merged, [t for t in proposed if merged[t] != policy.settings[t]]
                )
                out: list[s.PreviewTierOut] = []
                for tier in tiers:
                    # The same rule as a save: what would be enforced now, and from when the
                    # shorter period would apply.
                    waiting = m.plan_pending(
                        self._defaults, tier, policy.settings[tier], policy.pending.get(tier),
                        merged[tier], now, self._grace(), self._audit_floor,
                    )  # fmt: skip
                    enforced = m.enforced_days(
                        self._defaults, tier, merged[tier], waiting, now, self._audit_floor
                    )
                    cutoff = m.cutoff(now, days[tier])
                    count = (
                        None
                        if cutoff is None
                        else await measure_tier(
                            conn, principal.workspace_id, tier, cutoff, limits, now
                        )
                    )
                    out.append(
                        s.PreviewTierOut(
                            tier=tier.value,
                            days=days[tier],
                            forever=days[tier] is None,
                            enforced_days=enforced,
                            effective_at=waiting.effective_at if waiting else None,
                            cutoff=cutoff,
                            runs=count.runs if count else 0,
                            events=count.events if count else 0,
                            artifact_bytes=count.artifact_bytes if count else 0,
                            rows=count.rows if count else 0,
                            truncated=count.truncated if count else False,
                        )
                    )
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                raise preview_timeout() from None
            raise
        return s.RetentionPreviewOut(
            as_of=now,
            proposed=bool(proposed),
            tiers=out,
            note=(
                "Nothing is deleted by a preview. Tiers are counted separately, not added. "
                "Counts are for the new period once it takes effect."
            ),
        )

    async def reports(
        self, principal: Principal, workspace: str, limit: int
    ) -> s.RetentionReportsOut:
        self._own_workspace(principal, workspace)
        async with self._engine.connect() as conn:
            rows = await PolicyRepository(conn, principal.tenant).reports(limit)
        return s.RetentionReportsOut(
            items=[
                s.RetentionReportOut(
                    id=r.id,
                    tier=cast(s.ReportTierName, r.tier),
                    mode=cast(Literal["dry_run", "enforce"], r.mode),
                    runs=r.runs,
                    events=r.events,
                    artifact_bytes=r.artifact_bytes,
                    rows=r.rows,
                    truncated=r.truncated,
                    cutoff_at=r.cutoff_at,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
                for r in rows
            ]
        )
