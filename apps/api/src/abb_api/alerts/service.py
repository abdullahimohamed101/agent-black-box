"""Use cases behind the alerting routes (ADR-150): destinations, rules, preview and history.

Every change and its audit row commit in one transaction. Audit details name ids, the destination
type and the NAMES of the fields that changed; they never hold a target, a recipient, a secret or
any free text. Destinations are decrypted nowhere in this module: the sealed columns are written
and never read back.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.alerts import model as m
from abb_api.alerts import schemas as s
from abb_api.alerts import sealed
from abb_api.alerts.delivery import open_destination, registry_for, send_opened
from abb_api.alerts.jobs import ensure_sweep
from abb_api.alerts.notifiers import Alert
from abb_api.alerts.payload import build_test_payload
from abb_api.alerts.repository import (
    DestinationRepository,
    NameTakenError,
    NotificationRepository,
    RuleRepository,
)
from abb_api.alerts.spend import SpendReader
from abb_api.alerts.subjects import SubjectLoader
from abb_api.alerts.targets import (
    TargetInvalid,
    mask_recipients,
    validate_recipients,
    validate_web_url,
)
from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.clock import Clock
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.db import tables as t
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, retry_after_seconds
from abb_api.jobs.outbox import OutboxRepository
from abb_api.projects.repository import ProjectRepository
from abb_api.runs import cursors

logger = logging.getLogger(__name__)

PAGE_MAX = 200


# ---------------------------------------------------------------- errors (fixed messages)


def destination_not_found() -> AppError:
    return AppError(
        "ALERT_DESTINATION_NOT_FOUND",
        "Destination not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def rule_not_found() -> AppError:
    return AppError(
        "ALERT_RULE_NOT_FOUND",
        "Alert rule not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def project_missing() -> AppError:
    return AppError(
        "PROJECT_NOT_FOUND", "Project not found.", category=ErrorCategory.NOT_FOUND, status_code=404
    )


def target_invalid(reason: str) -> AppError:
    return AppError(
        "DESTINATION_TARGET_INVALID",
        "The destination target is not acceptable.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"reason": reason},
    )


def destination_invalid(reason: str) -> AppError:
    return AppError(
        "DESTINATION_INVALID",
        "The change does not apply to this destination.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"reason": reason},
    )


def email_not_configured() -> AppError:
    return AppError(
        "EMAIL_NOT_CONFIGURED",
        "Email destinations need the operator to configure SMTP (ABB_SMTP_HOST and related "
        "settings).",
        category=ErrorCategory.VALIDATION,
        status_code=422,
    )


def rule_invalid(field: str, reason: str) -> AppError:
    return AppError(
        "RULE_INVALID",
        "The rule is not valid for its trigger.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"field": field, "reason": reason},
    )


def name_taken(kind: str) -> AppError:
    return AppError(
        "ALERT_NAME_TAKEN",
        f"A {kind} with this name already exists.",
        category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def limit_reached(what: str) -> AppError:
    return AppError(
        "ALERT_LIMIT_REACHED",
        f"The workspace has reached its limit of {what}.",
        category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def _rate_limited(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "Alert changes are limited per workspace; try again shortly.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def preview_timeout() -> AppError:
    return AppError(
        "ALERT_PREVIEW_TIMEOUT",
        "The preview took too long; try a shorter window.",
        category=ErrorCategory.TIMEOUT,
        status_code=504,
        retryable=True,
    )


# ---------------------------------------------------------------- rule validation

# Which filters each trigger accepts, and which of them it needs.
ALLOWED_FILTERS = {
    m.APPROVAL_REQUESTED: frozenset({"agent"}),
    m.APPROVAL_EXPIRING: frozenset({"agent", "expiring_lead_seconds"}),
    m.SECURITY_FINDING: frozenset({"agent", "min_severity"}),
    m.RUN_FAILED: frozenset({"agent", "run_statuses"}),
    m.SPEND_OVER: frozenset({"agent", "threshold_usd", "period"}),
}
REQUIRED_FILTERS = {
    m.APPROVAL_EXPIRING: ("expiring_lead_seconds",),
    m.SECURITY_FINDING: ("min_severity",),
    m.SPEND_OVER: ("threshold_usd",),
}


def normalise_filters(trigger: str, filters: s.RuleFilters) -> dict[str, Any]:
    """The stored filter object, or a 422 naming the field (never its value)."""
    given = filters.model_dump(exclude_none=True)
    for field in given:
        if field not in ALLOWED_FILTERS[trigger]:
            raise rule_invalid(field, "not_applicable")
    for field in REQUIRED_FILTERS.get(trigger, ()):
        if field not in given:
            raise rule_invalid(field, "required")
    if trigger == m.RUN_FAILED and "run_statuses" in given:
        given["run_statuses"] = sorted(set(given["run_statuses"]))
    if trigger == m.SPEND_OVER:
        given["threshold_usd"] = float(round(Decimal(str(given["threshold_usd"])), 2))
        if given["threshold_usd"] <= 0:
            raise rule_invalid("threshold_usd", "too_small")
        given.setdefault("period", "day")
    return given


def _who(principal: Principal) -> str:
    return principal.actor_id[:128]


class AlertsService:
    def __init__(self, engine: AsyncEngine, settings: Settings, clock: Clock) -> None:
        self._engine = engine
        self._settings = settings
        self._clock = clock
        per_minute = settings.alerts_writes_per_minute
        self._write_limiter = InMemoryRateLimiter(
            events_per_second=per_minute / 60,
            burst_events=per_minute,
            bytes_per_second=1.0,
            burst_bytes=1,
        )
        self._preview_limiter = InMemoryRateLimiter(
            events_per_second=20 / 60, burst_events=20, bytes_per_second=1.0, burst_bytes=1
        )
        tests = settings.alerts_tests_per_minute
        self._test_limiter = InMemoryRateLimiter(
            events_per_second=tests / 60, burst_events=tests, bytes_per_second=1.0, burst_bytes=1
        )

    def _throttle(
        self, principal: Principal, *, preview: bool = False, send_test: bool = False
    ) -> None:
        limiter = self._preview_limiter if preview else self._write_limiter
        if send_test:
            wait = self._test_limiter.acquire(str(principal.workspace_id), events=1, bytes_=0)
            if wait is not None:
                raise _rate_limited(wait)
        wait = limiter.acquire(str(principal.workspace_id), events=1, bytes_=0)
        if wait is not None:
            raise _rate_limited(wait)

    async def _audit(
        self,
        conn: AsyncConnection,
        principal: Principal,
        action: str,
        resource_kind: str,
        resource_id: str,
        **details: Any,
    ) -> None:
        await AuditRepository(conn, principal.tenant).append(
            AuditEntry(
                actor_kind="user",
                actor_id=principal.actor_id,
                action=action,
                resource_kind=resource_kind,
                resource_id=resource_id,
                details=details,
            )
        )

    # ------------------------------------------------------------------ destinations

    def _out(self, row: m.DestinationRow) -> s.DestinationOut:
        return s.DestinationOut(
            id=public_id(IdKind.DESTINATION, row.id),
            name=row.name,
            type=row.type,  # type: ignore[arg-type]
            masked_target=row.masked_target,
            state=row.state,  # type: ignore[arg-type]
            consecutive_failures=row.consecutive_failures,
            paused_reason=row.paused_reason,  # type: ignore[arg-type]
            rule_count=row.rule_count,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )

    def _target_config(
        self, dest_type: str, target: s.DestinationTarget
    ) -> tuple[dict[str, Any], str]:
        """The config to seal and its masked display form, from a target of the right shape."""
        try:
            if dest_type == m.EMAIL:
                if target.url is not None or target.recipients is None:
                    raise TargetInvalid("target_shape")
                recipients = validate_recipients(target.recipients)
                return {"recipients": recipients}, mask_recipients(recipients)
            if target.recipients is not None or target.url is None:
                raise TargetInvalid("target_shape")
            web = validate_web_url(target.url)
            return {"url": web.url}, web.masked
        except TargetInvalid as invalid:
            raise target_invalid(invalid.reason) from None

    async def list_destinations(self, principal: Principal) -> s.DestinationList:
        async with self._engine.connect() as conn:
            rows = await DestinationRepository(conn, principal.tenant).list_all()
        return s.DestinationList(
            items=[self._out(r) for r in rows], email_configured=self._settings.smtp_configured
        )

    async def get_destination(self, principal: Principal, raw_id: str) -> s.DestinationOut:
        destination_id = parse_public_id(IdKind.DESTINATION, raw_id)
        if destination_id is None:
            raise destination_not_found()
        async with self._engine.connect() as conn:
            row = await DestinationRepository(conn, principal.tenant).get(destination_id)
        if row is None:
            raise destination_not_found()
        return self._out(row)

    async def create_destination(
        self, principal: Principal, body: s.DestinationCreate
    ) -> s.DestinationCreated:
        self._throttle(principal)
        if body.type == m.EMAIL and not self._settings.smtp_configured:
            raise email_not_configured()
        config, masked = self._target_config(body.type, body.target)
        destination_id = new_uuid(IdKind.DESTINATION)
        workspace_id = principal.workspace_id
        config_blob = await sealed.seal(
            self._engine,
            sealed.encode_config(config),
            purpose="config",
            workspace_id=workspace_id,
            destination_id=destination_id,
        )
        signing_secret: str | None = None
        signing_blob: bytes | None = None
        if body.type == m.WEBHOOK:
            signing_secret = sealed.new_signing_secret()
            signing_blob = await sealed.seal(
                self._engine,
                signing_secret.encode(),
                purpose="signing",
                workspace_id=workspace_id,
                destination_id=destination_id,
            )
        who = _who(principal)
        async with self._engine.begin() as conn:
            repository = DestinationRepository(conn, principal.tenant)
            if await repository.count() >= self._settings.alerts_max_destinations_per_workspace:
                raise limit_reached("destinations")
            try:
                await repository.insert(
                    id=destination_id,
                    name=body.name,
                    type=body.type,
                    config_encrypted=config_blob,
                    signing_secret_encrypted=signing_blob,
                    key_id=sealed.key_id_of(config_blob),
                    masked_target=masked,
                    created_by=who,
                    updated_by=who,
                )
            except NameTakenError:
                raise name_taken("destination") from None
            await self._audit(
                conn,
                principal,
                "alert.destination.create",
                "alert_destination",
                public_id(IdKind.DESTINATION, destination_id),
                type=body.type,
            )
            row = await repository.get(destination_id)
        assert row is not None
        return s.DestinationCreated(**self._out(row).model_dump(), signing_secret=signing_secret)

    async def update_destination(
        self, principal: Principal, raw_id: str, body: s.DestinationPatch
    ) -> s.DestinationCreated:
        """Rename, replace the target, pause or resume, rotate the signing secret."""
        self._throttle(principal)
        destination_id = parse_public_id(IdKind.DESTINATION, raw_id)
        if destination_id is None:
            raise destination_not_found()
        workspace_id = principal.workspace_id
        # Sealing needs the key (its own transaction), so it happens before the row is locked;
        # what is sealed depends only on the request and the (immutable) type.
        async with self._engine.connect() as conn:
            current = await DestinationRepository(conn, principal.tenant).get(destination_id)
        if current is None:
            raise destination_not_found()
        values: dict[str, Any] = {}
        changed: list[str] = []
        audit_kinds: list[str] = []
        new_secret: str | None = None
        if body.name is not None and body.name != current.name:
            values["name"] = body.name
            changed.append("name")
        if body.target is not None:
            config, masked = self._target_config(current.type, body.target)
            if current.type == m.EMAIL and not self._settings.smtp_configured:
                raise email_not_configured()
            blob = await sealed.seal(
                self._engine,
                sealed.encode_config(config),
                purpose="config",
                workspace_id=workspace_id,
                destination_id=destination_id,
            )
            values.update(
                config_encrypted=blob,
                key_id=sealed.key_id_of(blob),
                masked_target=masked,
                consecutive_failures=0,
            )
            if current.state == m.FAILING:
                values["state"] = m.ACTIVE
            changed.append("target")
        if body.rotate_signing_secret:
            if current.type != m.WEBHOOK:
                raise destination_invalid("no_signing_secret")
            new_secret = sealed.new_signing_secret()
            values["signing_secret_encrypted"] = await sealed.seal(
                self._engine,
                new_secret.encode(),
                purpose="signing",
                workspace_id=workspace_id,
                destination_id=destination_id,
            )
            audit_kinds.append("rotate_secret")
        if body.state == m.PAUSED and current.state != m.PAUSED:
            values.update(state=m.PAUSED, paused_reason="manual")
            audit_kinds.append("pause")
        elif body.state == m.ACTIVE and current.state in (m.PAUSED, m.FAILING):
            values.update(state=m.ACTIVE, paused_reason=None, consecutive_failures=0)
            audit_kinds.append("resume")
        if changed:
            audit_kinds.insert(0, "update")
        who = _who(principal)
        async with self._engine.begin() as conn:
            repository = DestinationRepository(conn, principal.tenant)
            if values:
                try:
                    await repository.update(destination_id, updated_by=who, **values)
                except NameTakenError:
                    raise name_taken("destination") from None
            resource = public_id(IdKind.DESTINATION, destination_id)
            for kind in audit_kinds:
                await self._audit(
                    conn,
                    principal,
                    f"alert.destination.{kind}",
                    "alert_destination",
                    resource,
                    type=current.type,
                    **({"changed": changed} if kind == "update" else {}),
                )
            row = await repository.get(destination_id)
        if row is None:
            raise destination_not_found()  # deleted by a concurrent request
        return s.DestinationCreated(**self._out(row).model_dump(), signing_secret=new_secret)

    async def test_destination(self, principal: Principal, raw_id: str) -> s.DestinationTestResult:
        """Send a fixed test alert through the same sender as real deliveries, now, and say how it
        went. A "send test" proves the target works; it changes nothing about the destination:
        the failure counter, the state and the history are not touched (a person debugging a URL
        must not pause it, and a test must not hide a real outage by resetting the counter).
        The database connection is released before the network wait."""
        self._throttle(principal)
        self._throttle(principal, send_test=True)
        destination_id = parse_public_id(IdKind.DESTINATION, raw_id)
        if destination_id is None:
            raise destination_not_found()
        async with self._engine.connect() as conn:
            row = await DestinationRepository(conn, principal.tenant).get(destination_id)
            if row is None:
                raise destination_not_found()
            slug = await SubjectLoader(conn, principal.tenant).workspace_slug()
            try:
                opened = await open_destination(conn, principal.workspace_id, row)
            except (sealed.SealError, ValueError):
                opened = None
        ok: bool
        code: str | None
        if opened is None:
            ok, code = False, "config_unreadable"
        else:
            now = self._clock().astimezone(UTC)
            alert_id = new_uuid(IdKind.ALERT)
            result = await send_opened(
                registry_for(self._settings),
                opened,
                Alert(
                    alert_id=public_id(IdKind.ALERT, alert_id),
                    payload=build_test_payload(
                        workspace_id=principal.workspace_id,
                        alert_id=alert_id,
                        workspace_slug=slug,
                        created_at=now,
                    ),
                    attempt=1,
                    web_origin=self._settings.web_origin_normalised,
                    test=True,
                ),
            )
            ok, code = result.ok, result.error_code
        async with self._engine.begin() as conn:
            await self._audit(
                conn,
                principal,
                "alert.destination.test",
                "alert_destination",
                public_id(IdKind.DESTINATION, destination_id),
                type=row.type,
                ok=ok,
                **({"error_code": code} if code else {}),
            )
        return s.DestinationTestResult(ok=ok, error_code=code)

    async def delete_destination(self, principal: Principal, raw_id: str) -> None:
        self._throttle(principal)
        destination_id = parse_public_id(IdKind.DESTINATION, raw_id)
        if destination_id is None:
            raise destination_not_found()
        async with self._engine.begin() as conn:
            destinations = DestinationRepository(conn, principal.tenant)
            row = await destinations.get(destination_id)
            if row is None:
                raise destination_not_found()
            # Alerts still waiting for this destination are closed honestly before its row goes
            # (the foreign key then clears the reference on the history rows).
            await NotificationRepository(conn, principal.tenant).skip_pending_of_destination(
                destination_id, "destination_deleted"
            )
            rule_ids = await destinations.rule_ids_of(destination_id)
            await destinations.delete(destination_id)
            disabled = await RuleRepository(conn, principal.tenant).disable_without_destinations(
                rule_ids
            )
            await self._audit(
                conn,
                principal,
                "alert.destination.delete",
                "alert_destination",
                public_id(IdKind.DESTINATION, destination_id),
                type=row.type,
                rules_disabled=disabled,
            )

    # ------------------------------------------------------------------ rules

    async def _rule_out(
        self,
        conn: AsyncConnection,
        principal: Principal,
        rules: list[m.RuleRow],
    ) -> list[s.RuleOut]:
        manager = holds(principal, actions.ALERT_DESTINATION_READ)
        refs: dict[uuid.UUID, s.RuleDestinationRef] = {}
        if manager and rules:
            wanted = {d for r in rules for d in r.destination_ids}
            for row in await DestinationRepository(conn, principal.tenant).list_all():
                if row.id in wanted:
                    refs[row.id] = s.RuleDestinationRef(
                        id=public_id(IdKind.DESTINATION, row.id),
                        name=row.name,
                        type=row.type,  # type: ignore[arg-type]
                        state=row.state,  # type: ignore[arg-type]
                    )
        return [
            s.RuleOut(
                id=public_id(IdKind.ALERT_RULE, r.id),
                name=r.name,
                trigger=r.trigger,  # type: ignore[arg-type]
                project_id=public_id(IdKind.PROJECT, r.project_id) if r.project_id else None,
                filters=s.RuleFilters(**r.filters),
                cooldown_seconds=r.cooldown_seconds,
                enabled=r.enabled,
                last_fired_at=r.last_fired_at,
                destination_count=len(r.destination_ids),
                destinations=[refs[d] for d in r.destination_ids if d in refs],
                created_by=r.created_by,
                created_at=r.created_at,
                updated_by=r.updated_by,
                updated_at=r.updated_at,
            )
            for r in rules
        ]

    async def list_rules(self, principal: Principal) -> s.RuleList:
        async with self._engine.connect() as conn:
            rules = await RuleRepository(conn, principal.tenant).list_all()
            return s.RuleList(items=await self._rule_out(conn, principal, rules))

    async def get_rule(self, principal: Principal, raw_id: str) -> s.RuleOut:
        rule_id = parse_public_id(IdKind.ALERT_RULE, raw_id)
        if rule_id is None:
            raise rule_not_found()
        async with self._engine.connect() as conn:
            rule = await RuleRepository(conn, principal.tenant).get(rule_id)
            if rule is None:
                raise rule_not_found()
            return (await self._rule_out(conn, principal, [rule]))[0]

    async def _check_scope(
        self,
        conn: AsyncConnection,
        principal: Principal,
        project_id: uuid.UUID | None,
        trigger: str,
        filters: dict[str, Any],
        destination_ids: list[uuid.UUID],
    ) -> None:
        """The project, the agent and the destinations must belong to the caller's workspace.
        Foreign and unknown ids are the same 404 (the agent filter, a name, is a 422)."""
        if project_id is not None:
            if await ProjectRepository(conn, principal.tenant).get(project_id) is None:
                raise project_missing()
        found = await DestinationRepository(conn, principal.tenant).existing_ids(destination_ids)
        if found != set(destination_ids):
            raise destination_not_found()
        agent = filters.get("agent")
        if agent is not None:
            query = select(1).where(
                t.agents.c.workspace_id == principal.workspace_id, t.agents.c.slug == agent
            )
            if project_id is not None:
                query = query.where(t.agents.c.project_id == project_id)
            if await conn.scalar(query.limit(1)) is None:
                raise rule_invalid("agent", "unknown_agent")

    @staticmethod
    def _destination_ids(raw: list[str]) -> list[uuid.UUID]:
        parsed = [parse_public_id(IdKind.DESTINATION, r) for r in raw]
        if any(p is None for p in parsed):
            raise destination_not_found()
        unique = sorted({p for p in parsed if p is not None}, key=lambda i: i.bytes)
        return unique

    async def create_rule(self, principal: Principal, body: s.RuleCreate) -> s.RuleOut:
        self._throttle(principal)
        filters = normalise_filters(body.trigger, body.filters)
        if len(set(body.destination_ids)) > self._settings.alerts_max_destinations_per_rule:
            raise limit_reached("destinations per rule")
        destination_ids = self._destination_ids(body.destination_ids)
        project_id = None
        if body.project_id is not None:
            project_id = parse_public_id(IdKind.PROJECT, body.project_id)
            if project_id is None:
                raise project_missing()
        rule_id = new_uuid(IdKind.ALERT_RULE)
        who = _who(principal)
        async with self._engine.begin() as conn:
            rules = RuleRepository(conn, principal.tenant)
            if await rules.count() >= self._settings.alerts_max_rules_per_workspace:
                raise limit_reached("rules")
            await self._check_scope(
                conn, principal, project_id, body.trigger, filters, destination_ids
            )
            try:
                await rules.insert(
                    destination_ids,
                    id=rule_id,
                    project_id=project_id,
                    name=body.name,
                    trigger=body.trigger,
                    filters=filters,
                    cooldown_seconds=body.cooldown_seconds,
                    enabled=body.enabled,
                    created_by=who,
                    updated_by=who,
                )
            except NameTakenError:
                raise name_taken("rule") from None
            if body.enabled and body.trigger in m.SCHEDULED_TRIGGERS:
                await ensure_sweep(OutboxRepository(conn, principal.tenant), principal.tenant)
            await self._audit(
                conn,
                principal,
                "alert.rule.create",
                "alert_rule",
                public_id(IdKind.ALERT_RULE, rule_id),
                trigger=body.trigger,
                destinations=len(destination_ids),
            )
            row = await rules.get(rule_id)
            assert row is not None
            return (await self._rule_out(conn, principal, [row]))[0]

    async def update_rule(self, principal: Principal, raw_id: str, body: s.RulePatch) -> s.RuleOut:
        self._throttle(principal)
        rule_id = parse_public_id(IdKind.ALERT_RULE, raw_id)
        if rule_id is None:
            raise rule_not_found()
        given = body.model_fields_set
        who = _who(principal)
        async with self._engine.begin() as conn:
            rules = RuleRepository(conn, principal.tenant)
            current = await rules.get(rule_id)
            if current is None:
                raise rule_not_found()
            values: dict[str, Any] = {}
            changed: list[str] = []
            project_id = current.project_id
            if "project_id" in given:
                project_id = (
                    parse_public_id(IdKind.PROJECT, body.project_id) if body.project_id else None
                )
                if body.project_id is not None and project_id is None:
                    raise project_missing()
                values["project_id"] = project_id
                changed.append("project_id")
            filters = current.filters
            if "filters" in given and body.filters is not None:
                filters = normalise_filters(current.trigger, body.filters)
                values["filters"] = filters
                changed.append("filters")
            destination_ids = list(current.destination_ids)
            if "destination_ids" in given and body.destination_ids is not None:
                if len(set(body.destination_ids)) > self._settings.alerts_max_destinations_per_rule:
                    raise limit_reached("destinations per rule")
                destination_ids = self._destination_ids(body.destination_ids)
                changed.append("destination_ids")
            if "name" in given and body.name is not None and body.name != current.name:
                values["name"] = body.name
                changed.append("name")
            if "cooldown_seconds" in given and body.cooldown_seconds is not None:
                values["cooldown_seconds"] = body.cooldown_seconds
                changed.append("cooldown_seconds")
            enabled = current.enabled
            if "enabled" in given and body.enabled is not None and body.enabled != current.enabled:
                enabled = body.enabled
                values["enabled"] = enabled
                changed.append("enabled")
            if "project_id" in given or "filters" in given or "destination_ids" in given:
                await self._check_scope(
                    conn, principal, project_id, current.trigger, filters, destination_ids
                )
            if changed:
                try:
                    await rules.update(rule_id, updated_by=who, **values)
                except NameTakenError:
                    raise name_taken("rule") from None
                if "destination_ids" in changed:
                    await rules.set_destinations(rule_id, destination_ids)
                if enabled and current.trigger in m.SCHEDULED_TRIGGERS:
                    await ensure_sweep(OutboxRepository(conn, principal.tenant), principal.tenant)
                await self._audit(
                    conn,
                    principal,
                    "alert.rule.update",
                    "alert_rule",
                    public_id(IdKind.ALERT_RULE, rule_id),
                    trigger=current.trigger,
                    changed=changed,
                )
            row = await rules.get(rule_id)
            assert row is not None
            return (await self._rule_out(conn, principal, [row]))[0]

    async def delete_rule(self, principal: Principal, raw_id: str) -> None:
        self._throttle(principal)
        rule_id = parse_public_id(IdKind.ALERT_RULE, raw_id)
        if rule_id is None:
            raise rule_not_found()
        async with self._engine.begin() as conn:
            rules = RuleRepository(conn, principal.tenant)
            current = await rules.get(rule_id)
            if current is None:
                raise rule_not_found()
            await NotificationRepository(conn, principal.tenant).skip_pending_of_rule(
                rule_id, "rule_deleted"
            )
            await rules.delete(rule_id)
            await self._audit(
                conn,
                principal,
                "alert.rule.delete",
                "alert_rule",
                public_id(IdKind.ALERT_RULE, rule_id),
                trigger=current.trigger,
            )

    # ------------------------------------------------------------------ preview

    async def preview_rule(self, principal: Principal, raw_id: str, days: int) -> s.RulePreview:
        """How often the rule would have fired over the last `days`, from recorded metadata only.

        Counts, never rows; bounded in time (statement timeout) and in window."""
        self._throttle(principal, preview=True)
        rule_id = parse_public_id(IdKind.ALERT_RULE, raw_id)
        if rule_id is None:
            raise rule_not_found()
        now = self._clock().astimezone(UTC)
        since = now - timedelta(days=days)
        try:
            async with self._engine.begin() as conn:
                timeout_ms = int(self._settings.alerts_preview_timeout_seconds * 1000)
                await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                rule = await RuleRepository(conn, principal.tenant).get(rule_id)
                if rule is None:
                    raise rule_not_found()
                daily = await _preview_counts(conn, principal, rule, since, now)
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                raise preview_timeout() from None
            raise
        return s.RulePreview(
            days=days,
            count=sum(daily.values()),
            daily=[s.PreviewDay(day=d, count=n) for d, n in sorted(daily.items())],
        )

    # ------------------------------------------------------------------ history

    async def list_notifications(
        self,
        principal: Principal,
        *,
        rule_id: str | None,
        destination_id: str | None,
        status: str | None,
        trigger: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> s.NotificationPage:
        rule = parse_public_id(IdKind.ALERT_RULE, rule_id) if rule_id else None
        destination = (
            parse_public_id(IdKind.DESTINATION, destination_id) if destination_id else None
        )
        if (rule_id and rule is None) or (destination_id and destination is None):
            return s.NotificationPage(items=[], next_cursor=None)  # same as an id with no rows
        after: tuple[datetime, uuid.UUID] | None = None
        if cursor is not None:
            key = cursors.decode(cursor, "alerts", width=2).key
            after = (cursors.as_datetime(key[0]), cursors.as_uuid(key[1]))
        async with self._engine.connect() as conn:
            rows = await NotificationRepository(conn, principal.tenant).page(
                rule_id=rule,
                destination_id=destination,
                status=status,
                trigger=trigger,
                since=since,
                until=until,
                limit=limit + 1,
                after=after,
            )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = cursors.encode(
                cursors.Cursor("alerts", [last.created_at.isoformat(), str(last.id)])
            )
        manager = holds(principal, actions.ALERT_DESTINATION_READ)
        sees_approvals = holds(principal, actions.APPROVAL_READ)
        return s.NotificationPage(
            items=[self._notification_out(r, manager, sees_approvals) for r in page],
            next_cursor=next_cursor,
        )

    @staticmethod
    def _notification_out(
        row: m.NotificationRow, manager: bool, sees_approvals: bool
    ) -> s.NotificationOut:
        def pid(kind: IdKind, value: uuid.UUID | None) -> str | None:
            return from_uuid(kind, value) if value is not None else None

        return s.NotificationOut(
            id=from_uuid(IdKind.ALERT, row.id),
            rule=(
                s.NotificationRule(id=from_uuid(IdKind.ALERT_RULE, row.rule_id), name=row.rule_name)
                if row.rule_id is not None and row.rule_name is not None
                else None
            ),
            destination=(
                s.NotificationDestination(
                    id=from_uuid(IdKind.DESTINATION, row.destination_id) if manager else None,
                    name=row.destination_name if manager else None,
                    type=row.destination_type,  # type: ignore[arg-type]
                )
                if row.destination_id is not None and row.destination_type is not None
                else None
            ),
            trigger=row.trigger,  # type: ignore[arg-type]
            severity=row.severity,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            skip_reason=row.skip_reason,  # type: ignore[arg-type]
            attempts=row.attempts,
            error_code=row.last_error_code,
            project_id=pid(IdKind.PROJECT, row.project_id),
            run_id=pid(IdKind.RUN, row.run_id),
            finding_id=pid(IdKind.FINDING, row.finding_id),
            approval_id=pid(IdKind.APPROVAL, row.approval_id) if sees_approvals else None,
            agent_slug=row.agent_slug,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
            next_attempt_at=row.next_attempt_at,
        )


async def _preview_counts(
    conn: AsyncConnection,
    principal: Principal,
    rule: m.RuleRow,
    since: datetime,
    now: datetime,
) -> dict[date, int]:
    """Per UTC day, how many facts in the window match the rule (the same predicates the live
    matcher applies, evaluated over stored rows)."""
    ws = principal.workspace_id
    trigger, flt = rule.trigger, rule.filters
    agent = flt.get("agent")

    def day_of(column: Any) -> Any:
        return func.date(func.timezone("UTC", column))

    if trigger in (m.APPROVAL_REQUESTED, m.APPROVAL_EXPIRING):
        a = t.approvals.c
        conditions: list[Any] = [a.workspace_id == ws]
        if rule.project_id is not None:
            conditions.append(a.project_id == rule.project_id)
        if agent is not None:
            conditions.append(a.agent_id == agent)
        when: Any
        if trigger == m.APPROVAL_REQUESTED:
            conditions.append(a.requested_at >= since)
            when = a.requested_at
        else:
            # It would have fired if the approval was still waiting at (expires_at - lead), and
            # that moment fell inside the window and had passed.
            lead = timedelta(seconds=int(flt.get("expiring_lead_seconds") or 0))
            moment = a.expires_at - lead
            end_of_wait = func.coalesce(a.decided_at, a.closed_at)
            conditions += [
                moment >= since,
                moment <= now,
                a.requested_at <= moment,
                func.coalesce(end_of_wait > moment, True),
            ]
            when = moment
        day = day_of(when)  # one expression object, so GROUP BY matches the select list
        rows = await conn.execute(
            select(day.label("day"), func.count().label("n")).where(*conditions).group_by(day)
        )
        return {r.day: int(r.n) for r in rows}
    if trigger == m.SECURITY_FINDING:
        f = t.findings.c
        wanted = [
            name
            for name, rank in m.SEVERITY_RANK.items()
            if rank >= m.SEVERITY_RANK.get(str(flt.get("min_severity")), 0)
        ]
        conditions = [
            f.workspace_id == ws,
            f.detector.like("sec.%"),
            f.created_at >= since,
            f.severity.in_(wanted),
        ]
        if rule.project_id is not None:
            conditions.append(f.project_id == rule.project_id)
        if agent is not None:
            conditions.append(f.agent_slug == agent)
        day = day_of(f.created_at)
        rows = await conn.execute(
            select(day.label("day"), func.count().label("n")).where(*conditions).group_by(day)
        )
        return {r.day: int(r.n) for r in rows}
    if trigger == m.RUN_FAILED:
        r = t.runs.c
        statuses = flt.get("run_statuses") or list(m.RUN_FAIL_STATUSES)
        finished = func.coalesce(r.completed_at, r.started_at)
        conditions = [r.workspace_id == ws, r.status.in_(statuses), finished >= since]
        if rule.project_id is not None:
            conditions.append(r.project_id == rule.project_id)
        if agent is not None:
            conditions.append(r.agent_slug == agent)
        day = day_of(finished)
        rows = await conn.execute(
            select(day.label("day"), func.count().label("n")).where(*conditions).group_by(day)
        )
        return {r_.day: int(r_.n) for r_ in rows}
    # spend_over: days whose total exceeded the threshold
    threshold = Decimal(str(flt.get("threshold_usd", 0)))
    first = since.date()
    last = now.date()
    totals = await SpendReader(conn, principal.tenant).daily_totals(
        first,
        last + timedelta(days=1),
        project_id=rule.project_id,
        agent_slug=agent,
    )
    return {day: 1 for day, total in totals.items() if threshold > 0 and total > threshold}
