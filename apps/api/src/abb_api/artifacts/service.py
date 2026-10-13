"""Artifact use cases: validate, hash, store, and read in bounded chunks (ADR-030)."""

import asyncio
import hashlib
import logging
import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.repository import ArtifactRecord, ArtifactRepository
from abb_api.artifacts.schemas import ArtifactChunk, ArtifactOut
from abb_api.artifacts.store import ArtifactMissing, ArtifactStore, ArtifactStoreError, valid_key
from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import public_id
from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.ingestion.service import project_key_required, rate_limited
from abb_api.runs.queries import RunQueries
from abb_api.security.redaction import ArtifactDecision, ServerRedactor
from abb_api.security.settings import CaptureMode, PgSecuritySettingsStore

logger = logging.getLogger(__name__)

ALLOWED_MEDIA_TYPES = frozenset({"text/plain", "application/json", "application/octet-stream"})
_UTF8_CONTINUATION = 0x80, 0xBF


def artifact_not_found() -> AppError:
    return AppError(
        "ARTIFACT_NOT_FOUND",
        "Artifact not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def _store_unavailable() -> AppError:
    return AppError(
        "ARTIFACT_STORE_UNAVAILABLE",
        "The artifact store is temporarily unavailable.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
        headers={"Retry-After": "2"},
    )


def artifact_out(record: ArtifactRecord) -> ArtifactOut:
    return ArtifactOut(
        id=public_id(IdKind.ARTIFACT, record.id),
        run_id=public_id(IdKind.RUN, record.run_id),
        project_id=public_id(IdKind.PROJECT, record.project_id),
        kind=record.kind,  # type: ignore[arg-type]
        name=record.name,
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256.hex(),
        created_at=record.created_at,
    )


def _conflict() -> AppError:
    # One message for every cause (other content, another project's id): it must not reveal which.
    return AppError(
        "ARTIFACT_CONFLICT",
        "That artifact id is already in use (artifacts are immutable and ids are not reusable).",
        category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def storage_key(workspace_id: uuid.UUID, artifact_id: uuid.UUID, sha256: bytes) -> str:
    return f"{workspace_id.hex}/{artifact_id.hex}-{sha256.hex()}"


def align_to_character(data: bytes, end: int) -> int:
    """Move `end` back (at most 3 bytes) so a chunk does not stop inside a UTF-8 sequence."""
    low, high = _UTF8_CONTINUATION
    for _ in range(3):
        if 0 < end < len(data) and low <= data[end] <= high:
            end -= 1
        else:
            break
    return end


class ArtifactService:
    def __init__(
        self,
        engine: AsyncEngine,
        store: ArtifactStore,
        limiter: RateLimiter,
        clock: Clock,
        *,
        max_bytes: int,
        redactor: ServerRedactor | None = None,
        scan_max_bytes: int = 4 * 1024 * 1024,
        scan_max_seconds: float = 10.0,
    ) -> None:
        self._engine = engine
        self._store = store
        self._limiter = limiter
        self._clock = clock
        self._max_bytes = max_bytes
        self._redactor = redactor or ServerRedactor()
        self._scan_max_bytes = scan_max_bytes
        self._scan_max_seconds = scan_max_seconds

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def put(
        self,
        principal: Principal,
        *,
        artifact_id: uuid.UUID,
        run_id: uuid.UUID,
        kind: str,
        name: str | None,
        media_type: str,
        data: bytes,
        claimed_sha256: str | None,
    ) -> tuple[ArtifactOut, bool]:
        """Store an artifact. Returns the record and whether it was newly created."""
        if principal.project_id is None:
            raise project_key_required()
        raw_digest = digest = hashlib.sha256(data).digest()
        if claimed_sha256 is not None and claimed_sha256.lower() != digest.hex():
            raise AppError(
                "ARTIFACT_HASH_MISMATCH",
                "The body does not match X-Content-SHA256.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
        # Its own bucket: a burst of artifacts must not starve event ingestion (and vice versa).
        wait = self._limiter.acquire(
            f"artifacts:{principal.project_id}", events=1, bytes_=len(data)
        )
        if wait is not None:
            raise rate_limited(wait)

        async with self._engine.begin() as conn:
            repo = ArtifactRepository(conn, principal.tenant)
            existing = await repo.get(artifact_id, project_id=None)
            run = await RunQueries(conn, principal.tenant, None).get(run_id)
        if run is not None and run.project_id != principal.project_id:
            raise AppError(
                "RUN_PROJECT_MISMATCH",
                "That run id belongs to another project.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
            )
        if existing is not None:
            return await self._existing(principal, existing, data=data, digest=raw_digest)

        # Redact before storing (ADR-130): what is hashed, sized and written is the stored form.
        async with self._engine.connect() as conn:
            mode = (
                await PgSecuritySettingsStore(conn, principal.tenant).get(principal.project_id)
            ).capture_mode
        decision = await self._decide(data, mode)
        if name is not None:
            # A label is free text too: a client that skips redaction can put a key in it.
            name = self._redactor.label(name, max_seconds=self._scan_max_seconds)
        stored = decision.body
        digest = hashlib.sha256(stored).digest()
        key = storage_key(principal.workspace_id, artifact_id, digest)
        try:
            await self._store.put(key, stored)
        except ArtifactStoreError:
            logger.error("artifact store write failed")
            raise _store_unavailable() from None
        record = ArtifactRecord(
            id=artifact_id,
            run_id=run_id,
            project_id=principal.project_id,
            kind=kind,
            name=name,
            media_type=media_type,
            size_bytes=len(stored),
            sha256=digest,
            storage_key=key,
            created_at=self._clock(),
            redaction=decision.record() if (decision.count or decision.dropped_reason) else None,
        )
        async with self._engine.begin() as conn:
            repo = ArtifactRepository(conn, principal.tenant)
            created = await repo.insert(record)
            winner = record if created else await repo.get(artifact_id, project_id=None)
        if not created:
            # A concurrent upload of the same id won. Our file is an orphan unless the content is
            # identical (then it is the same key, and must stay).
            if winner is None or winner.sha256 != digest:
                try:
                    await self._store.delete(key)
                except ArtifactStoreError:
                    logger.warning("could not remove an orphaned artifact file")
            assert winner is not None
            return await self._existing(principal, winner, data=data, digest=raw_digest)
        if decision.count or decision.dropped_reason:
            # Counts and kind names only (INV-5): the log never carries a value.
            logger.info(
                "artifact redacted",
                extra={"redactions": decision.count, "dropped_reason": decision.dropped_reason},
            )
        return artifact_out(record), True

    async def _decide(self, data: bytes, mode: CaptureMode) -> ArtifactDecision:
        # Off the event loop: a few MiB of text takes tens of milliseconds to scan.
        return await asyncio.to_thread(
            self._redactor.artifact_body,
            data,
            mode,
            max_bytes=self._scan_max_bytes,
            max_seconds=self._scan_max_seconds,
        )

    async def _existing(
        self, principal: Principal, record: ArtifactRecord, *, data: bytes, digest: bytes
    ) -> tuple[ArtifactOut, bool]:
        """A retry of an upload that was stored: the same id and content is a duplicate.

        The stored bytes are the redacted form, so a retry is compared as it would have been
        stored: the raw digest, or the FULL-mode redaction of the body (independent of the
        project's mode today, so a mode change does not turn a retry into a conflict). A body that
        was dropped has nothing to compare; its retry is a duplicate by id.
        """
        if record.project_id != principal.project_id:
            raise _conflict()
        if record.sha256 == digest:
            return artifact_out(record), False
        if record.redaction and record.redaction.get("dropped_reason"):
            return artifact_out(record), False
        if record.redaction:
            full = await self._decide(data, CaptureMode.FULL)
            if full.dropped_reason is None and hashlib.sha256(full.body).digest() == record.sha256:
                return artifact_out(record), False
        raise _conflict()

    async def _visible(self, principal: Principal, artifact_id: uuid.UUID) -> ArtifactRecord:
        async with self._engine.connect() as conn:
            record = await ArtifactRepository(conn, principal.tenant).get(
                artifact_id, project_id=principal.project_id
            )
        if record is None:
            raise artifact_not_found()
        return record

    async def get(self, principal: Principal, artifact_id: uuid.UUID) -> ArtifactOut:
        return artifact_out(await self._visible(principal, artifact_id))

    async def read_chunk(
        self, principal: Principal, artifact_id: uuid.UUID, *, offset: int, limit: int
    ) -> ArtifactChunk:
        record = await self._visible(principal, artifact_id)
        total = record.size_bytes
        if offset > total:
            raise AppError(
                "ARTIFACT_OFFSET_INVALID",
                "offset is beyond the end of the artifact.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
                details={"total_bytes": total},
            )
        if not valid_key(record.storage_key):  # a corrupt row must not become a path
            logger.error("artifact row has an invalid storage key")
            raise _store_unavailable()
        want = min(limit, total - offset)
        try:
            # Three bytes beyond the window let us see whether the cut falls inside a character.
            data = await self._store.read(record.storage_key, offset, want + 3)
        except ArtifactMissing:
            logger.error("artifact row has no file")
            raise AppError(
                "ARTIFACT_CONTENT_MISSING",
                "The artifact's content is no longer available.",
                category=ErrorCategory.NOT_FOUND,
                status_code=404,
            ) from None
        except ArtifactStoreError:
            logger.error("artifact store read failed")
            raise _store_unavailable() from None
        end = align_to_character(data, want) if offset + want < total else min(want, len(data))
        text = data[:end].decode("utf-8", errors="replace")
        next_offset = offset + end if offset + end < total else None
        return ArtifactChunk(
            id=public_id(IdKind.ARTIFACT, record.id),
            offset=offset,
            next_offset=next_offset,
            total_bytes=total,
            content=text,
        )
