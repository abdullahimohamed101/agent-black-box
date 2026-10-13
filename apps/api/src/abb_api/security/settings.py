"""Per-project security settings (ADR-130): capture mode, detector mutes, destination allowlist.

`SecuritySettingsStore` is the contract (INV-7); `PgSecuritySettingsStore` implements it. Every
statement carries the workspace and the project (INV-3). A project without a row has the defaults:
METADATA_ONLY, nothing muted, no allowlist. `SecuritySettingsService.update` is the only writer
that application code should call: it changes the row and appends the audit entry in one
transaction, so a change can never exist without its audit row.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from abb_event_schema.ids import IdKind
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.audit.repository import ActorKind, AuditEntry, AuditRepository
from abb_api.authz.principal import Principal
from abb_api.core.domain import DomainError, NotFoundError
from abb_api.db import tables as t
from abb_api.ids import public_id
from abb_api.tenancy import TenantContext

MAX_MUTED_DETECTORS = 64
MAX_ALLOWLIST_ENTRIES = 256
MAX_HOST_PATTERN_LENGTH = 100  # audit details keep values short (audit.repository)
AUDIT_LIST_ITEMS = 4  # entries named in an audit row; the counts always say how many changed

# Detector ids are `sec.<name>` (plan, "Contract between the parts"). Whether an id names a real
# detector is checked where detectors are known (the API of step 2), not by storage.
_DETECTOR_ID = re.compile(r"sec\.[a-z][a-z0-9_]{0,59}")
# A host name or IP literal, optionally with a leading `*.` that matches any subdomain.
_HOST_PATTERN = re.compile(
    r"(?:\*\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)


class CaptureMode(StrEnum):
    """How much the server stores of what an SDK sends. The SDK's `PayloadMode`, same meanings."""

    FULL = "FULL"  # payloads and artifacts stored after redaction
    METADATA_ONLY = "METADATA_ONLY"  # attributes and timings only (the default)
    DISABLED = "DISABLED"  # METADATA_ONLY, and no free text (error messages, run metadata)


DEFAULT_CAPTURE_MODE = CaptureMode.METADATA_ONLY


class InvalidSettingsError(DomainError):
    """A settings value is malformed. The message never echoes the value."""


@dataclass(frozen=True)
class SecuritySettings:
    project_id: uuid.UUID
    capture_mode: CaptureMode = DEFAULT_CAPTURE_MODE
    muted_detectors: tuple[str, ...] = ()
    destination_allowlist: tuple[str, ...] = ()
    updated_at: datetime | None = None  # None: no row yet, these are the defaults
    updated_by: str | None = None


def normalize_detector_ids(values: Iterable[str]) -> tuple[str, ...]:
    ids = sorted(set(values))
    if len(ids) > MAX_MUTED_DETECTORS:
        raise InvalidSettingsError(f"at most {MAX_MUTED_DETECTORS} detectors can be muted")
    if not all(_DETECTOR_ID.fullmatch(i) for i in ids):
        raise InvalidSettingsError("a detector id must look like 'sec.secret_file_read'")
    return tuple(ids)


def normalize_host_patterns(values: Iterable[str]) -> tuple[str, ...]:
    hosts = sorted({v.strip().lower().rstrip(".") for v in values})
    if len(hosts) > MAX_ALLOWLIST_ENTRIES:
        raise InvalidSettingsError(f"at most {MAX_ALLOWLIST_ENTRIES} allowlist entries")
    for host in hosts:
        if len(host) > MAX_HOST_PATTERN_LENGTH or not _HOST_PATTERN.fullmatch(host):
            raise InvalidSettingsError(
                "an allowlist entry must be a host name such as 'api.example.com' or "
                "'*.example.com' (no scheme, port or path)"
            )
    return tuple(hosts)


@dataclass(frozen=True)
class Actor:
    """Who changed the settings; goes to `updated_by` and the audit row."""

    kind: ActorKind
    id: str

    @staticmethod
    def of(principal: Principal) -> "Actor":
        return Actor("user" if principal.kind == "user" else "api_key", principal.actor_id)


class SecuritySettingsStore(Protocol):
    """Storage contract for one tenant's settings (INV-7)."""

    async def get(self, project_id: uuid.UUID) -> SecuritySettings: ...

    async def list(self) -> list[SecuritySettings]: ...

    async def upsert(
        self,
        project_id: uuid.UUID,
        *,
        updated_by: str,
        capture_mode: CaptureMode | None = None,
        muted_detectors: Sequence[str] | None = None,
        destination_allowlist: Sequence[str] | None = None,
    ) -> SecuritySettings: ...


def _settings(row: Any) -> SecuritySettings:
    return SecuritySettings(
        project_id=row.project_id,
        capture_mode=CaptureMode(row.capture_mode),
        muted_detectors=tuple(row.muted_detectors),
        destination_allowlist=tuple(row.destination_allowlist),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


class PgSecuritySettingsStore:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def get(self, project_id: uuid.UUID, *, lock: bool = False) -> SecuritySettings:
        """The project's settings, or the defaults. With `lock`, the project row is locked first
        (`FOR NO KEY UPDATE`, like other per-project serialisation) so concurrent changes read and
        write one after another; a project that is not in this workspace is `NotFoundError`."""
        if lock:
            found = await self._conn.execute(
                select(t.projects.c.id)
                .where(
                    t.projects.c.workspace_id == self._tenant.workspace_id,
                    t.projects.c.id == project_id,
                )
                .with_for_update(key_share=True)
            )
            if found.first() is None:
                raise NotFoundError("project not found")
        c = t.project_security_settings.c
        row = (
            await self._conn.execute(
                select(t.project_security_settings).where(
                    c.workspace_id == self._tenant.workspace_id, c.project_id == project_id
                )
            )
        ).first()
        return _settings(row) if row is not None else SecuritySettings(project_id)

    async def list(self) -> list[SecuritySettings]:
        """Projects that have a stored row (every other project has the defaults)."""
        c = t.project_security_settings.c
        rows = await self._conn.execute(
            select(t.project_security_settings)
            .where(c.workspace_id == self._tenant.workspace_id)
            .order_by(c.project_id)
        )
        return [_settings(r) for r in rows]

    async def upsert(
        self,
        project_id: uuid.UUID,
        *,
        updated_by: str,
        capture_mode: CaptureMode | None = None,
        muted_detectors: Sequence[str] | None = None,
        destination_allowlist: Sequence[str] | None = None,
    ) -> SecuritySettings:
        """Create the row or change the given fields (None leaves a field as it is)."""
        mutes = None if muted_detectors is None else list(normalize_detector_ids(muted_detectors))
        hosts = (
            None
            if destination_allowlist is None
            else list(normalize_host_patterns(destination_allowlist))
        )
        table = t.project_security_settings
        insert_values: dict[str, Any] = {
            "workspace_id": self._tenant.workspace_id,
            "project_id": project_id,
            "capture_mode": (capture_mode or DEFAULT_CAPTURE_MODE).value,
            "muted_detectors": mutes or [],
            "destination_allowlist": hosts or [],
            "updated_by": updated_by,
        }
        statement = insert(table).values(**insert_values)
        changes: dict[str, Any] = {"updated_by": updated_by, "updated_at": func.now()}
        for name, given in (
            ("capture_mode", capture_mode),
            ("muted_detectors", mutes),
            ("destination_allowlist", hosts),
        ):
            if given is not None:
                changes[name] = statement.excluded[name]
        row = (
            await self._conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["workspace_id", "project_id"], set_=changes
                ).returning(*table.c)
            )
        ).first()
        assert row is not None
        return _settings(row)


def _delta(before: Sequence[str], after: Sequence[str]) -> tuple[list[str], list[str]]:
    return sorted(set(after) - set(before)), sorted(set(before) - set(after))


def audit_details(before: SecuritySettings, after: SecuritySettings) -> dict[str, Any]:
    """What changed, bounded for the audit log. Names the first few entries and counts the rest."""
    details: dict[str, Any] = {"fields": []}
    fields: list[str] = details["fields"]
    if before.capture_mode != after.capture_mode:
        fields.append("capture_mode")
        details["capture_mode_from"] = before.capture_mode.value
        details["capture_mode_to"] = after.capture_mode.value
    for field_name, key in (
        ("muted_detectors", "muted"),
        ("destination_allowlist", "allowlist"),
    ):
        old, new = getattr(before, field_name), getattr(after, field_name)
        added, removed = _delta(old, new)
        if added or removed:
            fields.append(field_name)
            details[f"{key}_added"] = added[:AUDIT_LIST_ITEMS]
            details[f"{key}_removed"] = removed[:AUDIT_LIST_ITEMS]
            details[f"{key}_added_count"] = len(added)
            details[f"{key}_removed_count"] = len(removed)
    return details


async def update_settings(
    conn: AsyncConnection,
    tenant: TenantContext,
    project_id: uuid.UUID,
    actor: Actor,
    *,
    capture_mode: CaptureMode | None = None,
    muted_detectors: Sequence[str] | None = None,
    destination_allowlist: Sequence[str] | None = None,
) -> SecuritySettings:
    """Apply the given fields and append the audit entry, on the caller's transaction.

    A request that changes nothing writes nothing (and no audit entry). The project row is locked
    for the duration, so concurrent changes are applied one after another and each audit entry
    describes exactly the change it made.
    """
    mutes = None if muted_detectors is None else normalize_detector_ids(muted_detectors)
    hosts = (
        None if destination_allowlist is None else normalize_host_patterns(destination_allowlist)
    )
    store = PgSecuritySettingsStore(conn, tenant)
    before = await store.get(project_id, lock=True)
    wanted = SecuritySettings(
        project_id,
        capture_mode or before.capture_mode,
        before.muted_detectors if mutes is None else mutes,
        before.destination_allowlist if hosts is None else hosts,
    )
    details = audit_details(before, wanted)
    if not details["fields"]:
        return before
    after = await store.upsert(
        project_id,
        updated_by=actor.id[:128],
        capture_mode=wanted.capture_mode,
        muted_detectors=wanted.muted_detectors,
        destination_allowlist=wanted.destination_allowlist,
    )
    await AuditRepository(conn, tenant).append(
        AuditEntry(
            actor_kind=actor.kind,
            actor_id=actor.id,
            action="security.settings.update",
            resource_kind="project",
            resource_id=public_id(IdKind.PROJECT, project_id),
            details=details,
        )
    )
    return after


class SecuritySettingsService:
    """Reads and changes settings for the API (step 2) with one transaction per change."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, tenant: TenantContext, project_id: uuid.UUID) -> SecuritySettings:
        async with self._engine.connect() as conn:
            return await PgSecuritySettingsStore(conn, tenant).get(project_id)

    async def update(
        self,
        tenant: TenantContext,
        project_id: uuid.UUID,
        actor: Actor,
        *,
        capture_mode: CaptureMode | None = None,
        muted_detectors: Sequence[str] | None = None,
        destination_allowlist: Sequence[str] | None = None,
    ) -> SecuritySettings:
        async with self._engine.begin() as conn:
            return await update_settings(
                conn,
                tenant,
                project_id,
                actor,
                capture_mode=capture_mode,
                muted_detectors=muted_detectors,
                destination_allowlist=destination_allowlist,
            )
