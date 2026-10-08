"""Artifact use cases: validate, hash, store, and read in bounded chunks (ADR-030)."""

import hashlib
import logging
import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.repository import ArtifactRecord, ArtifactRepository
from abb_api.artifacts.schemas import ArtifactChunk, ArtifactOut
from abb_api.artifacts.store import ArtifactStore, ArtifactStoreError, valid_key
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import public_id
from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.ingestion.service import project_key_required, rate_limited
from abb_api.tenancy import Principal

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


def _conflict(message: str) -> AppError:
    return AppError(
        "ARTIFACT_CONFLICT",
        message,
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
    ) -> None:
        self._engine = engine
        self._store = store
        self._limiter = limiter
        self._clock = clock
        self._max_bytes = max_bytes

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
        digest = hashlib.sha256(data).digest()
        if claimed_sha256 is not None and claimed_sha256.lower() != digest.hex():
            raise AppError(
                "ARTIFACT_HASH_MISMATCH",
                "The body does not match X-Content-SHA256.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
        wait = self._limiter.acquire(str(principal.project_id), events=1, bytes_=len(data))
        if wait is not None:
            raise rate_limited(wait)

        async with self._engine.begin() as conn:
            repo = ArtifactRepository(conn, principal.tenant)
            existing = await repo.get(artifact_id, project_id=None)
        if existing is not None:
            return self._existing(
                principal, existing, project_id=principal.project_id, digest=digest
            )

        key = storage_key(principal.workspace_id, artifact_id, digest)
        try:
            await self._store.put(key, data)
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
            size_bytes=len(data),
            sha256=digest,
            storage_key=key,
            created_at=self._clock(),
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
            return self._existing(principal, winner, project_id=principal.project_id, digest=digest)
        return artifact_out(record), True

    def _existing(
        self,
        principal: Principal,
        record: ArtifactRecord,
        *,
        project_id: uuid.UUID,
        digest: bytes,
    ) -> tuple[ArtifactOut, bool]:
        if record.project_id != project_id:
            raise _conflict("That artifact id is already in use.")
        if record.sha256 != digest:
            raise _conflict("That artifact id holds different content; artifacts are immutable.")
        return artifact_out(record), False

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
