"""Rotate the alert-encryption key and re-seal every destination (ADR-150, ADR-162).

Rotation is two separate, individually safe facts:

1. A newer `alerts-encryption` row in the append-only `server_secrets` table (`new_key=True`).
   Running API and worker processes cache the current key, so they keep sealing under the OLD key
   until they are restarted; everything they already sealed keeps opening.
2. Every blob re-sealed under the newest key. A destination is one transaction: its row is locked
   (`FOR UPDATE`, so a concurrent edit waits), both blobs are opened with the key named in their
   own header, re-sealed with the SAME associated data (purpose, workspace, destination: the
   binding is rebuilt from the row, never copied), the new blobs are opened again and compared
   with the old plaintext, and only then written. A blob already under the newest key is skipped,
   so a rerun resumes where an interrupted run stopped and a second run changes nothing.

The final verdict is computed from the blobs' own key-id headers: "old keys can be retired" only
when no blob of any destination references a key other than the newest. Old key rows are never
deleted here (the runtime role cannot, and retirement is a deliberate owner action).
No plaintext is returned, logged or printed: results are counts and destination ids.
"""

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.alerts import sealed
from abb_api.alerts.sealed import Purpose, SealError

Hook = Callable[[uuid.UUID, str], Awaitable[None]]  # (destination id, phase): tests inject faults


@dataclass
class RotationReport:
    dry_run: bool
    newest_key_id: int | None
    key_added: int | None = None
    destinations: int = 0
    resealed: int = 0  # destinations changed by this run
    would_reseal: int = 0
    already_current: int = 0
    failed: list[uuid.UUID] = field(default_factory=list)
    blobs_by_key: Counter[int] = field(default_factory=Counter)  # after this run
    unreadable_blobs: int = 0  # a header that names no key, or is not a sealed blob
    truncated: bool = False  # --max stopped the run before every destination was seen

    @property
    def older_blobs(self) -> int:
        newest = self.newest_key_id
        return sum(n for k, n in self.blobs_by_key.items() if k != newest) + self.unreadable_blobs

    @property
    def can_retire_old_keys(self) -> bool:
        return (
            not self.dry_run
            and not self.failed
            and not self.truncated
            and self.newest_key_id is not None
            and self.older_blobs == 0
        )


def _key_of(blob: bytes | None) -> int | None:
    if blob is None:
        return None
    try:
        return sealed.key_id_of(blob)
    except SealError:
        return -1  # not a sealed blob: counted as unreadable, never as "current"


async def _reseal_one(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    destination_id: uuid.UUID,
    newest: int,
    hook: Hook | None,
) -> bool:
    """Re-seal one destination in its own transaction. True if it changed."""
    async with engine.connect() as conn, conn.begin():
        row = (
            await conn.execute(
                text(
                    "SELECT config_encrypted, signing_secret_encrypted FROM alert_destinations "
                    "WHERE workspace_id = :w AND id = :d FOR UPDATE"
                ),
                {"w": workspace_id, "d": destination_id},
            )
        ).first()
        if row is None:
            return False  # deleted since the scan
        old = {"config": bytes(row.config_encrypted)}
        if row.signing_secret_encrypted is not None:
            old["signing"] = bytes(row.signing_secret_encrypted)
        new: dict[str, bytes] = {}
        for purpose, blob in old.items():
            if _key_of(blob) == newest:
                new[purpose] = blob
                continue
            plain = await sealed.open_sealed(
                engine, blob, purpose=_purpose(purpose),
                workspace_id=workspace_id, destination_id=destination_id,
            )  # fmt: skip
            fresh = await sealed.seal(
                engine, plain, purpose=_purpose(purpose),
                workspace_id=workspace_id, destination_id=destination_id,
            )  # fmt: skip
            if sealed.key_id_of(fresh) != newest or (
                await sealed.open_sealed(
                    engine,
                    fresh,
                    purpose=_purpose(purpose),
                    workspace_id=workspace_id,
                    destination_id=destination_id,
                )
                != plain
            ):
                raise SealError  # never write a blob that does not open to the same plaintext
            new[purpose] = fresh
        if new == old:
            return False
        if hook is not None:
            await hook(destination_id, "before_write")
        await conn.execute(
            text(
                "UPDATE alert_destinations SET config_encrypted = :c, "
                "signing_secret_encrypted = :s, key_id = :k "
                "WHERE workspace_id = :w AND id = :d"
            ),
            {
                "c": new["config"], "s": new.get("signing"), "k": sealed.key_id_of(new["config"]),
                "w": workspace_id, "d": destination_id,
            },
        )  # fmt: skip
        if hook is not None:
            await hook(destination_id, "before_commit")
    return True


def _purpose(name: str) -> Purpose:
    return "signing" if name == "signing" else "config"


async def _scan(
    conn: AsyncConnection, after: tuple[uuid.UUID, uuid.UUID] | None, batch: int
) -> list[tuple[uuid.UUID, uuid.UUID, bytes, bytes | None]]:
    where = "WHERE (workspace_id, id) > (:w, :i)" if after else ""
    rows = await conn.execute(
        text(
            "SELECT workspace_id, id, config_encrypted, signing_secret_encrypted "  # noqa: S608
            f"FROM alert_destinations {where} ORDER BY workspace_id, id LIMIT :n"
        ),
        {"n": batch, **({"w": after[0], "i": after[1]} if after else {})},
    )
    return [
        (
            r.workspace_id,
            r.id,
            bytes(r.config_encrypted),
            r.signing_secret_encrypted and bytes(r.signing_secret_encrypted),
        )
        for r in rows
    ]


async def rotate_alerts(
    url: str,
    *,
    dry_run: bool,
    new_key: bool,
    batch_size: int = 50,
    max_destinations: int = 100_000,
    hook: Hook | None = None,
    progress: Callable[..., None] = lambda *a, **k: None,
) -> RotationReport:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            newest = await sealed.newest_key_id(conn)
        report = RotationReport(dry_run=dry_run, newest_key_id=newest)
        if new_key and not dry_run:
            async with engine.begin() as conn:
                report.key_added = await sealed.add_key(conn)
            report.newest_key_id = report.key_added
            # A fresh engine: the old one may have cached the previous current key.
            await engine.dispose()
            engine = create_async_engine(url, poolclass=NullPool)
        elif new_key and dry_run:
            progress("rotate.would_add_key")
        newest = report.newest_key_id
        after: tuple[uuid.UUID, uuid.UUID] | None = None
        batches = 0
        while True:
            async with engine.connect() as conn:
                page = await _scan(conn, after, batch_size)
            if not page:
                break
            batches += 1
            for workspace_id, destination_id, config, signing in page:
                after = (workspace_id, destination_id)
                if report.destinations >= max_destinations:
                    report.truncated = True
                    break
                report.destinations += 1
                keys = [k for k in (_key_of(config), _key_of(signing)) if k is not None]
                stale = newest is not None and any(k != newest for k in keys)
                if newest is None or not stale:
                    report.already_current += 1
                    _tally(report, keys)
                    continue
                if dry_run:
                    report.would_reseal += 1
                    _tally(report, keys)
                    continue
                try:
                    changed = await _reseal_one(engine, workspace_id, destination_id, newest, hook)
                except Exception:  # one bad destination never stops the others; id only
                    report.failed.append(destination_id)
                    _tally(report, keys)
                    continue
                if changed:
                    report.resealed += 1
                    keys = [newest] * len(keys)
                else:
                    report.already_current += 1
                _tally(report, keys)
            progress(
                "rotate.progress",
                batch=batches,
                seen=report.destinations,
                resealed=report.resealed,
                failed=len(report.failed),
            )
            if report.truncated:
                break
        return report
    finally:
        await engine.dispose()


def _tally(report: RotationReport, keys: list[int]) -> None:
    for key in keys:
        if key == -1:
            report.unreadable_blobs += 1
        else:
            report.blobs_by_key[key] += 1
