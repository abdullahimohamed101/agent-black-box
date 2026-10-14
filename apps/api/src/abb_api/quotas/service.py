"""Use cases behind the quota routes: the usage view and the audited overrides (ADR-154)."""

import uuid
from datetime import UTC, date, datetime, timedelta

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.domain import NotFoundError
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id
from abb_api.projects.access import authorise_project, project_not_found
from abb_api.projects.repository import ProjectRepository
from abb_api.quotas import schemas as s
from abb_api.quotas.model import BOUNDS, Quota, QuotaLimits, QuotaOverrides, status_of
from abb_api.quotas.repository import QuotaRepository
from abb_api.security.settings import Actor
from abb_api.tenancy import TenantContext

QUOTA_AUDIT_ACTION = "project.quota.update"
_DAILY = {Quota.RUNS_PER_DAY, Quota.EVENTS_PER_DAY}
_AGENT_COUNT_CAP = BOUNDS[Quota.AGENTS][1] + 1  # an exact figure for any permitted override


def quota_settings_invalid(quota: Quota) -> AppError:
    low, high = BOUNDS[quota]
    return AppError(
        "QUOTA_SETTINGS_INVALID",
        f"The {quota.value} quota must be a whole number from {low} to {high}.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"quota": quota.value},
    )


class QuotaApiService:
    def __init__(
        self, engine: AsyncEngine, defaults: QuotaLimits, clock: Clock, *, enforced: bool
    ) -> None:
        self._engine = engine
        self._defaults = defaults
        self._clock = clock
        self._enforced = enforced

    async def usage(self, principal: Principal, project_id: str) -> s.ProjectQuotasOut:
        async with self._engine.connect() as conn:
            scope = await authorise_project(conn, principal, project_id)
            assert scope is not None
            return await self._view(conn, principal.tenant, scope)

    async def update(
        self, principal: Principal, project_id: str, body: s.QuotaOverridesIn
    ) -> s.ProjectQuotasOut:
        parsed = parse_public_id(IdKind.PROJECT, project_id)
        if parsed is None:
            raise project_not_found()
        given = {q: getattr(body, q.value) for q in Quota if q.value in body.model_fields_set}
        for quota, value in given.items():
            low, high = BOUNDS[quota]
            if value is not None and not low <= value <= high:
                raise quota_settings_invalid(quota)
        actor = Actor.of(principal)
        try:
            async with self._engine.begin() as conn:
                if await ProjectRepository(conn, principal.tenant).get(parsed) is None:
                    raise project_not_found()
                repo = QuotaRepository(conn, principal.tenant)
                await repo.lock_project(parsed)
                before, _, _ = await repo.overrides(parsed)
                merged = {q: before.of(q) for q in Quota} | given
                after = QuotaOverrides(**{q.value: v for q, v in merged.items()})
                changed = [q for q in Quota if before.of(q) != after.of(q)]
                if changed:
                    await repo.write_overrides(parsed, after, updated_by=actor.id[:128])
                    audit = AuditRepository(conn, principal.tenant)
                    for quota in changed:
                        # Names and numbers only (old and new): the whole of what changed.
                        await audit.append(
                            AuditEntry(
                                actor_kind=actor.kind,
                                actor_id=actor.id,
                                action=QUOTA_AUDIT_ACTION,
                                resource_kind="project",
                                resource_id=from_uuid(IdKind.PROJECT, parsed),
                                details={
                                    "quota": quota.value,
                                    "old": before.of(quota),
                                    "new": after.of(quota),
                                },
                            )
                        )
                return await self._view(conn, principal.tenant, parsed)
        except NotFoundError:
            raise project_not_found() from None

    async def _view(
        self, conn: AsyncConnection, tenant: TenantContext, project_id: uuid.UUID
    ) -> s.ProjectQuotasOut:
        now = self._clock().astimezone(UTC)
        day: date = now.date()
        repo = QuotaRepository(conn, tenant)
        state = await repo.state(project_id, day, lock=False)
        if state is None:
            raise project_not_found()
        overrides, updated_by, updated_at = await repo.overrides(project_id)
        limits = state.limits(self._defaults)
        used = {
            Quota.AGENTS: await repo.count_agents(project_id, _AGENT_COUNT_CAP),
            Quota.RUNS_PER_DAY: state.runs_today,
            Quota.EVENTS_PER_DAY: state.events_today,
            Quota.ARTIFACT_BYTES: await repo.artifact_bytes(project_id),
        }
        rows = [
            s.QuotaUsageOut(
                quota=q.value,
                period="day" if q in _DAILY else "total",
                limit=limits.of(q),
                default=self._defaults.of(q),
                overridden=overrides.of(q) is not None,
                used=used[q],
                percent=used[q] * 100 // limits.of(q),
                status=status_of(used[q], limits.of(q)),
            )
            for q in Quota
        ]
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        return s.ProjectQuotasOut(
            project_id=from_uuid(IdKind.PROJECT, project_id),
            day=day,
            resets_at=midnight,
            enforced=self._enforced,
            quotas=rows,
            updated_at=updated_at if isinstance(updated_at, datetime) else None,
            updated_by=updated_by,
        )
