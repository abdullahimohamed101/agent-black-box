"""Persistence of destinations, rules and notifications: every statement carries the workspace
(INV-3) and no method returns anything a response may not show (ciphertext only travels in
`DestinationRow`, which no schema exposes)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts.model import (
    DestinationRow,
    NotificationRow,
    RuleRow,
    RuleTarget,
)
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

_D = t.alert_destinations.c
_R = t.alert_rules.c
_L = t.alert_rule_destinations.c
_N = t.alert_notifications.c


class NameTakenError(Exception):
    """The name is already used by another destination (or rule) of the workspace."""


def _destination(row: Any, rule_count: int = 0) -> DestinationRow:
    return DestinationRow(
        id=row.id,
        name=row.name,
        type=row.type,
        masked_target=row.masked_target,
        state=row.state,
        consecutive_failures=row.consecutive_failures,
        paused_reason=row.paused_reason,
        key_id=row.key_id,
        config_encrypted=bytes(row.config_encrypted),
        signing_secret_encrypted=(
            bytes(row.signing_secret_encrypted) if row.signing_secret_encrypted else None
        ),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        rule_count=rule_count,
    )


class DestinationRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    async def count(self) -> int:
        return int(
            await self._conn.scalar(
                select(func.count())
                .select_from(t.alert_destinations)
                .where(_D.workspace_id == self._ws)
            )
            or 0
        )

    async def list_all(self) -> list[DestinationRow]:
        counts = (
            select(_L.destination_id, func.count().label("n"))
            .where(_L.workspace_id == self._ws)
            .group_by(_L.destination_id)
            .subquery()
        )
        rows = await self._conn.execute(
            select(t.alert_destinations, func.coalesce(counts.c.n, 0).label("rule_count"))
            .outerjoin(counts, counts.c.destination_id == _D.id)
            .where(_D.workspace_id == self._ws)
            .order_by(_D.created_at, _D.id)
        )
        return [_destination(r, int(r.rule_count)) for r in rows]

    async def get(self, destination_id: uuid.UUID) -> DestinationRow | None:
        row = (
            await self._conn.execute(
                select(t.alert_destinations).where(
                    _D.workspace_id == self._ws, _D.id == destination_id
                )
            )
        ).first()
        if row is None:
            return None
        count = await self._conn.scalar(
            select(func.count()).where(
                _L.workspace_id == self._ws, _L.destination_id == destination_id
            )
        )
        return _destination(row, int(count or 0))

    async def existing_ids(self, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not ids:
            return set()
        rows = await self._conn.execute(
            select(_D.id).where(_D.workspace_id == self._ws, _D.id.in_(list(ids)))
        )
        return {r.id for r in rows}

    async def insert(self, **values: Any) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    insert(t.alert_destinations).values(workspace_id=self._ws, **values)
                )
        except IntegrityError as exc:
            if "uq_alert_destinations_name" in str(exc.orig):
                raise NameTakenError from None
            raise

    async def update(self, destination_id: uuid.UUID, **values: Any) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    update(t.alert_destinations)
                    .where(_D.workspace_id == self._ws, _D.id == destination_id)
                    .values(updated_at=func.now(), **values)
                )
        except IntegrityError as exc:
            if "uq_alert_destinations_name" in str(exc.orig):
                raise NameTakenError from None
            raise

    async def delete(self, destination_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            delete(t.alert_destinations).where(_D.workspace_id == self._ws, _D.id == destination_id)
        )
        return bool(result.rowcount)

    async def rule_ids_of(self, destination_id: uuid.UUID) -> list[uuid.UUID]:
        rows = await self._conn.execute(
            select(_L.rule_id).where(
                _L.workspace_id == self._ws, _L.destination_id == destination_id
            )
        )
        return [r.rule_id for r in rows]


class RuleRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    def _rule(self, row: Any, destination_ids: Sequence[uuid.UUID]) -> RuleRow:
        return RuleRow(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            trigger=row.trigger,
            filters=dict(row.filters),
            cooldown_seconds=row.cooldown_seconds,
            enabled=row.enabled,
            last_fired_at=row.last_fired_at,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
            destination_ids=tuple(destination_ids),
        )

    async def _links(self, rule_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
        if not rule_ids:
            return {}
        rows = await self._conn.execute(
            select(_L.rule_id, _L.destination_id)
            .where(_L.workspace_id == self._ws, _L.rule_id.in_(list(rule_ids)))
            .order_by(_L.destination_id)
        )
        found: dict[uuid.UUID, list[uuid.UUID]] = {}
        for r in rows:
            found.setdefault(r.rule_id, []).append(r.destination_id)
        return found

    async def count(self) -> int:
        return int(
            await self._conn.scalar(
                select(func.count()).select_from(t.alert_rules).where(_R.workspace_id == self._ws)
            )
            or 0
        )

    async def list_all(self) -> list[RuleRow]:
        rows = list(
            await self._conn.execute(
                select(t.alert_rules)
                .where(_R.workspace_id == self._ws)
                .order_by(_R.created_at, _R.id)
            )
        )
        links = await self._links([r.id for r in rows])
        return [self._rule(r, links.get(r.id, [])) for r in rows]

    async def get(self, rule_id: uuid.UUID) -> RuleRow | None:
        row = (
            await self._conn.execute(
                select(t.alert_rules).where(_R.workspace_id == self._ws, _R.id == rule_id)
            )
        ).first()
        if row is None:
            return None
        return self._rule(row, (await self._links([rule_id])).get(rule_id, []))

    async def insert(self, destination_ids: Sequence[uuid.UUID], **values: Any) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    insert(t.alert_rules).values(workspace_id=self._ws, **values)
                )
        except IntegrityError as exc:
            if "uq_alert_rules_name" in str(exc.orig):
                raise NameTakenError from None
            raise
        await self.set_destinations(values["id"], destination_ids)

    async def update(self, rule_id: uuid.UUID, **values: Any) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    update(t.alert_rules)
                    .where(_R.workspace_id == self._ws, _R.id == rule_id)
                    .values(updated_at=func.now(), **values)
                )
        except IntegrityError as exc:
            if "uq_alert_rules_name" in str(exc.orig):
                raise NameTakenError from None
            raise

    async def set_destinations(
        self, rule_id: uuid.UUID, destination_ids: Sequence[uuid.UUID]
    ) -> None:
        await self._conn.execute(
            delete(t.alert_rule_destinations).where(
                _L.workspace_id == self._ws, _L.rule_id == rule_id
            )
        )
        if destination_ids:
            await self._conn.execute(
                insert(t.alert_rule_destinations).values(
                    [
                        {"workspace_id": self._ws, "rule_id": rule_id, "destination_id": d}
                        for d in sorted(set(destination_ids), key=lambda d: d.bytes)
                    ]
                )
            )

    async def delete(self, rule_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            delete(t.alert_rules).where(_R.workspace_id == self._ws, _R.id == rule_id)
        )
        return bool(result.rowcount)

    async def disable_without_destinations(self, rule_ids: Sequence[uuid.UUID]) -> int:
        """After a destination was removed: a rule left with nowhere to send is switched off, so it
        is not a silent no-op that still looks enabled."""
        if not rule_ids:
            return 0
        result = await self._conn.execute(
            update(t.alert_rules)
            .where(
                _R.workspace_id == self._ws,
                _R.id.in_(list(rule_ids)),
                _R.enabled,
                ~select(_L.rule_id)
                .where(_L.workspace_id == self._ws, _L.rule_id == _R.id)
                .exists(),
            )
            .values(enabled=False, updated_at=func.now())
        )
        return int(result.rowcount)

    async def enabled_targets(self, trigger: str) -> list[RuleTarget]:
        """Enabled rules of a trigger with their destinations' states, in a stable order."""
        rules = list(
            await self._conn.execute(
                select(t.alert_rules)
                .where(_R.workspace_id == self._ws, _R.trigger == trigger, _R.enabled)
                .order_by(_R.id)
            )
        )
        if not rules:
            return []
        rows = await self._conn.execute(
            select(_L.rule_id, _L.destination_id, _D.state)
            .join(
                t.alert_destinations,
                and_(_D.workspace_id == _L.workspace_id, _D.id == _L.destination_id),
            )
            .where(_L.workspace_id == self._ws, _L.rule_id.in_([r.id for r in rules]))
            .order_by(_L.destination_id)
        )
        destinations: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        for r in rows:
            destinations.setdefault(r.rule_id, []).append((r.destination_id, r.state))
        return [
            RuleTarget(
                self._rule(r, [d for d, _ in destinations.get(r.id, [])]),
                tuple(destinations.get(r.id, [])),
            )
            for r in rules
        ]

    async def has_enabled(self, triggers: Sequence[str]) -> bool:
        found = await self._conn.scalar(
            select(1)
            .where(_R.workspace_id == self._ws, _R.enabled, _R.trigger.in_(list(triggers)))
            .limit(1)
        )
        return found is not None

    async def lock(self, rule_id: uuid.UUID) -> bool:
        """Serialise evaluations of one rule (cooldown reads then writes); False when the rule is
        gone or switched off by the time the lock is held."""
        enabled = await self._conn.scalar(
            select(_R.enabled)
            .where(_R.workspace_id == self._ws, _R.id == rule_id)
            .with_for_update()
        )
        return bool(enabled)

    async def mark_fired(self, rule_id: uuid.UUID, at: datetime) -> None:
        await self._conn.execute(
            update(t.alert_rules)
            .where(_R.workspace_id == self._ws, _R.id == rule_id)
            .values(last_fired_at=at)
        )


class NotificationRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    async def exists(self, rule_id: uuid.UUID, destination_id: uuid.UUID, dedupe_key: str) -> bool:
        found = await self._conn.scalar(
            select(1).where(
                _N.workspace_id == self._ws,
                _N.rule_id == rule_id,
                _N.destination_id == destination_id,
                _N.dedupe_key == dedupe_key,
            )
        )
        return found is not None

    async def made_since(
        self, rule_id: uuid.UUID, destination_id: uuid.UUID, since: datetime
    ) -> bool:
        """Cooldown: was a real (not skipped) notification created for this pair since `since`?"""
        found = await self._conn.scalar(
            select(1)
            .where(
                _N.workspace_id == self._ws,
                _N.rule_id == rule_id,
                _N.destination_id == destination_id,
                _N.created_at > since,
                _N.status != "skipped",
            )
            .limit(1)
        )
        return found is not None

    async def skipped_since(
        self, rule_id: uuid.UUID, destination_id: uuid.UUID, since: datetime
    ) -> int:
        return int(
            await self._conn.scalar(
                select(func.count()).where(
                    _N.workspace_id == self._ws,
                    _N.rule_id == rule_id,
                    _N.destination_id == destination_id,
                    _N.created_at > since,
                    _N.status == "skipped",
                )
            )
            or 0
        )

    async def insert(self, **values: Any) -> bool:
        """Insert unless this (rule, destination, event) already has a row. True when created."""
        result = await self._conn.execute(
            insert(t.alert_notifications)
            .values(workspace_id=self._ws, **values)
            .on_conflict_do_nothing(constraint="uq_alert_notifications_key")
            .returning(_N.id)
        )
        return result.first() is not None

    async def skip_pending_of_destination(self, destination_id: uuid.UUID, reason: str) -> int:
        result = await self._conn.execute(
            update(t.alert_notifications)
            .where(
                _N.workspace_id == self._ws,
                _N.destination_id == destination_id,
                _N.status == "pending",
            )
            .values(status="skipped", skip_reason=reason)
        )
        return int(result.rowcount)

    async def skip_pending_of_rule(self, rule_id: uuid.UUID, reason: str) -> int:
        result = await self._conn.execute(
            update(t.alert_notifications)
            .where(_N.workspace_id == self._ws, _N.rule_id == rule_id, _N.status == "pending")
            .values(status="skipped", skip_reason=reason)
        )
        return int(result.rowcount)

    async def page(
        self,
        *,
        rule_id: uuid.UUID | None,
        destination_id: uuid.UUID | None,
        status: str | None,
        trigger: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> list[NotificationRow]:
        conditions: list[ColumnElement[bool]] = [_N.workspace_id == self._ws]
        if rule_id is not None:
            conditions.append(_N.rule_id == rule_id)
        if destination_id is not None:
            conditions.append(_N.destination_id == destination_id)
        if status is not None:
            conditions.append(_N.status == status)
        if trigger is not None:
            conditions.append(_N.trigger == trigger)
        if since is not None:
            conditions.append(_N.created_at >= since)
        if until is not None:
            conditions.append(_N.created_at <= until)
        if after is not None:
            conditions.append(tuple_(_N.created_at, _N.id) < tuple_(*after))
        rows = await self._conn.execute(
            select(
                _N.id,
                _N.rule_id,
                _R.name.label("rule_name"),
                _N.destination_id,
                _D.name.label("destination_name"),
                _D.type.label("destination_type"),
                _N.trigger,
                _N.severity,
                _N.status,
                _N.skip_reason,
                _N.attempts,
                _N.last_error_code,
                _N.project_id,
                _N.run_id,
                _N.finding_id,
                _N.approval_id,
                _N.agent_slug,
                _N.created_at,
                _N.delivered_at,
                _N.next_attempt_at,
            )
            .select_from(
                t.alert_notifications.outerjoin(
                    t.alert_rules, and_(_R.workspace_id == _N.workspace_id, _R.id == _N.rule_id)
                ).outerjoin(
                    t.alert_destinations,
                    and_(_D.workspace_id == _N.workspace_id, _D.id == _N.destination_id),
                )
            )
            .where(*conditions)
            .order_by(_N.created_at.desc(), _N.id.desc())
            .limit(limit)
        )
        return [NotificationRow(**r._asdict()) for r in rows]
