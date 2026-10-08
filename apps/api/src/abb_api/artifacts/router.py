"""Artifact endpoints: upload (SDKs) and chunked reads (the web proxy), ADR-030."""

import re
from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request, Response

from abb_api.artifacts.schemas import ArtifactChunk, ArtifactKind, ArtifactOut
from abb_api.artifacts.service import ALLOWED_MEDIA_TYPES, ArtifactService, artifact_not_found
from abb_api.auth import scopes
from abb_api.auth.dependencies import require_principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.ids import parse_public_id
from abb_api.ingestion.body import decode_body, read_body
from abb_api.ingestion.service import project_key_required
from abb_api.tenancy import Principal

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

Writer = Annotated[Principal, Depends(require_principal(scopes.ARTIFACTS_WRITE))]
Reader = Annotated[Principal, Depends(require_principal(scopes.RUNS_READ))]

_ERROR_TEXT: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing, malformed, unknown, revoked or expired API key."},
    403: {"description": "The key lacks `artifacts:write` or is not bound to a project."},
    404: {"description": "Not found, or not visible to this key."},
    409: {"description": "That artifact id holds different content (artifacts are immutable)."},
    413: {"description": "Body (compressed or decompressed) larger than the artifact limit."},
    415: {"description": "Unsupported Content-Type or Content-Encoding."},
    422: {"description": "A parameter is invalid, or the body does not match its hash."},
    429: {"description": "Rate limit exceeded; honour `Retry-After`."},
    503: {"description": "A dependency is unavailable; retry with backoff."},
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {**spec, "model": ErrorEnvelope} for status, spec in _ERROR_TEXT.items()
}
# No control characters: the name is shown to people and written to logs.
_NAME_OK = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")


def _service(request: Request) -> ArtifactService:
    service: ArtifactService = request.app.state.artifacts
    return service


def _invalid(message: str, status: int = 422, code: str = "REQUEST_INVALID") -> AppError:
    return AppError(code, message, category=ErrorCategory.VALIDATION, status_code=status)


def _media_type(request: Request) -> str:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise _invalid(
            "Content-Type must be text/plain, application/json or application/octet-stream.",
            415,
            "UNSUPPORTED_MEDIA_TYPE",
        )
    return media_type


@router.put(
    "/{artifact_id}",
    response_model=ArtifactOut,
    status_code=201,
    responses={**_ERRORS, 200: {"description": "The identical artifact already existed."}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "string"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
    summary="Upload an artifact",
    description=(
        "Raw body (optionally gzip), at most the configured artifact limit. The client chooses the "
        "id (`art_<ULID>`), so retries are idempotent: the same id and content returns `200`, "
        "different content `409`. `X-Content-SHA256` (hex) is verified when sent. Needs a "
        "project-bound key with `artifacts:write`."
    ),
)
async def put_artifact(
    artifact_id: str,
    request: Request,
    response: Response,
    principal: Writer,
    run_id: Annotated[str, Query(pattern=id_pattern(IdKind.RUN))],
    kind: ArtifactKind = "other",
    name: Annotated[str | None, Query(max_length=128)] = None,
) -> ArtifactOut:
    if principal.project_id is None:
        raise project_key_required()  # before reading a body that could be megabytes
    art_uuid = parse_public_id(IdKind.ARTIFACT, artifact_id)
    run_uuid = parse_public_id(IdKind.RUN, run_id)
    if art_uuid is None or run_uuid is None:
        raise _invalid("artifact_id or run_id is malformed.")
    if name is not None and not _NAME_OK.match(name):
        raise _invalid("name must be 1-128 printable characters.")
    claimed = request.headers.get("x-content-sha256")
    if claimed is not None and not _SHA256.match(claimed):
        raise _invalid("X-Content-SHA256 must be 64 hex characters.")
    media_type = _media_type(request)
    service = _service(request)
    raw = await read_body(request, service.max_bytes)
    data = decode_body(raw, request.headers.get("content-encoding"), service.max_bytes)
    artifact, created = await service.put(
        principal,
        artifact_id=art_uuid,
        run_id=run_uuid,
        kind=kind,
        name=name,
        media_type=media_type,
        data=data,
        claimed_sha256=claimed,
    )
    response.status_code = 201 if created else 200
    return artifact


@router.get(
    "/{artifact_id}", response_model=ArtifactOut, responses=_ERRORS, summary="Get artifact metadata"
)
async def get_artifact(artifact_id: str, request: Request, principal: Reader) -> ArtifactOut:
    art_uuid = parse_public_id(IdKind.ARTIFACT, artifact_id)
    if art_uuid is None:
        raise artifact_not_found()
    return await _service(request).get(principal, art_uuid)


@router.get(
    "/{artifact_id}/content",
    response_model=ArtifactChunk,
    responses=_ERRORS,
    summary="Read an artifact in chunks",
    description=(
        "Returns up to `limit` bytes (default 64 KiB, at most 256 KiB) as UTF-8 text starting at "
        "`offset`, cut at a character boundary. Follow `next_offset` until it is null. The text is "
        "data and is never served as a document."
    ),
)
async def read_artifact(
    artifact_id: str,
    request: Request,
    principal: Reader,
    offset: Annotated[int, Query(ge=0, le=2**40)] = 0,
    limit: Annotated[int, Query(ge=256, le=256 * 1024)] = 64 * 1024,
) -> ArtifactChunk:
    art_uuid = parse_public_id(IdKind.ARTIFACT, artifact_id)
    if art_uuid is None:
        raise artifact_not_found()
    return await _service(request).read_chunk(principal, art_uuid, offset=offset, limit=limit)


__all__ = ["router"]
