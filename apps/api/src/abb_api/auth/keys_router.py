"""API key management for people (spec §92, D9). Keys never manage keys.

The token is returned once, in the creation response, with `Cache-Control: no-store`; only its hash
is stored. A person may create only keys that are no stronger than themselves, and a DEVELOPER may
revoke only keys they created (`Own(api_key.revoke)`).
"""

import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes as scope_names
from abb_api.auth.repository import ApiKeyRepository, StoredApiKey
from abb_api.authz import actions
from abb_api.authz.audit import audit_allowed, authorize_audited
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.authz.service import ungrantable_actions
from abb_api.core.domain import NotFoundError
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.ids import parse_public_id, public_id

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])

MAX_ACTIVE_KEYS = 200
MAX_LISTED = 500  # revoked keys are not listed; expired ones are, so this is a defensive bound

Reader = Annotated[Principal, Depends(require(actions.API_KEY_READ))]
Creator = Annotated[Principal, Depends(require(actions.API_KEY_CREATE))]
Revoker = Annotated[Principal, Depends(require(actions.API_KEY_REVOKE, owned=True))]

ScopeName = Literal["events:write", "runs:read", "artifacts:write", "policy:check"]
_INGESTION_SCOPES = frozenset({scope_names.EVENTS_WRITE, scope_names.ARTIFACTS_WRITE})
_KEY_ID = re.compile(r"[a-z0-9]{12}")
Name = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]


def _errors(**extra: str) -> dict[int | str, dict[str, Any]]:
    texts = {
        401: "Missing or invalid credential.",
        403: "The caller's role does not allow this.",
        404: "Workspace, project or key not found.",
        422: "The request is invalid.",
        503: "A dependency is unavailable; retry with backoff.",
        **{int(k[1:]): v for k, v in extra.items()},
    }
    return {code: {"description": text, "model": ErrorEnvelope} for code, text in texts.items()}


class ApiKeyOut(BaseModel):
    key_id: str = Field(description="The public identifier, not a secret.")
    name: str | None
    scopes: list[ScopeName]
    project_id: str | None
    created_by: str | None = Field(description="The person who made it; null for the CLI.")
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    status: Literal["active", "expired"]


class ApiKeyList(BaseModel):
    items: list[ApiKeyOut]


class CreateKey(BaseModel):
    name: Name | None = None
    scopes: list[ScopeName] = Field(min_length=1, max_length=4)
    project_id: str | None = Field(default=None, pattern=id_pattern(IdKind.PROJECT))
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class CreatedKey(BaseModel):
    key: ApiKeyOut
    token: str = Field(description="Shown once. Store it now; the server keeps only a hash.")


def _out(key: StoredApiKey, now: Any) -> ApiKeyOut:
    return ApiKeyOut(
        key_id=key.key_id,
        name=key.name,
        scopes=sorted(key.scopes),  # type: ignore[arg-type]
        project_id=public_id(IdKind.PROJECT, key.project_id) if key.project_id else None,
        created_by=public_id(IdKind.USER, key.created_by) if key.created_by else None,
        created_at=key.created_at.isoformat(),
        last_used_at=key.last_used_at.isoformat() if key.last_used_at else None,
        expires_at=key.expires_at.isoformat() if key.expires_at else None,
        status="active" if key.is_active(now) else "expired",
    )


def key_not_found() -> AppError:
    return AppError(
        "KEY_NOT_FOUND", "API key not found.", category=ErrorCategory.NOT_FOUND, status_code=404
    )


def _unprocessable(code: str, message: str, **details: object) -> AppError:
    return AppError(
        code, message, category=ErrorCategory.VALIDATION, status_code=422, details=dict(details)
    )


@router.get(
    "",
    response_model=ApiKeyList,
    responses=_errors(),
    summary="List the workspace's API keys",
    description="Keys that are not revoked. Secrets are never shown after creation.",
)
async def list_keys(request: Request, principal: Reader) -> ApiKeyList:
    engine: AsyncEngine = request.app.state.engine
    now = request.app.state.clock()
    async with engine.connect() as conn:
        keys = await ApiKeyRepository(conn, principal.tenant).list_current(MAX_LISTED)
    return ApiKeyList(items=[_out(k, now) for k in keys])


@router.post(
    "",
    status_code=201,
    response_model=CreatedKey,
    responses=_errors(c409="The workspace already has 200 active keys."),
    summary="Create an API key",
    description=(
        "The token is in this response only. Ingestion scopes need a `project_id`. A key can "
        "never do more than its creator can (`SCOPE_NOT_ALLOWED`)."
    ),
)
async def create_key(
    body: CreateKey, request: Request, response: Response, principal: Creator
) -> CreatedKey:
    scopes = frozenset(body.scopes)
    missing = ungrantable_actions(principal, scopes)
    if missing:
        raise _unprocessable(
            "SCOPE_NOT_ALLOWED",
            "A key cannot carry permissions its creator does not hold.",
            missing_permissions=sorted(missing),
        )
    if scopes & _INGESTION_SCOPES and body.project_id is None:
        raise _unprocessable("PROJECT_REQUIRED", "Ingestion keys must be bound to a project.")
    project = parse_public_id(IdKind.PROJECT, body.project_id) if body.project_id else None
    engine: AsyncEngine = request.app.state.engine
    now = request.app.state.clock()
    expires = now + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    async with engine.begin() as conn:
        keys = ApiKeyRepository(conn, principal.tenant)
        await keys.lock_for_create()
        if await keys.count_active(now) >= MAX_ACTIVE_KEYS:
            raise AppError(
                "LIMIT_REACHED",
                f"A workspace holds at most {MAX_ACTIVE_KEYS} active API keys.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
            )
        try:
            created = await keys.create(
                scopes=scopes,
                project_id=project,
                name=body.name,
                expires_at=expires,
                created_by=principal.user_id,
            )
        except NotFoundError:
            raise AppError(
                "PROJECT_NOT_FOUND",
                "Project not found.",
                category=ErrorCategory.NOT_FOUND,
                status_code=404,
            ) from None
    response.headers["Cache-Control"] = "no-store"
    await audit_allowed(
        request, principal, "api_key.create", resource_kind="api_key",
        resource_id=created.stored.key_id, scopes=sorted(scopes),
        project=body.project_id, expires_in_days=body.expires_in_days,
    )  # fmt: skip
    return CreatedKey(key=_out(created.stored, now), token=created.token)


@router.delete(
    "/{key_id}",
    status_code=204,
    responses=_errors(),
    summary="Revoke an API key",
    description=(
        "Takes effect on the key's next request. Owners, admins and security may revoke any key; "
        "a developer only keys they created."
    ),
)
async def revoke_key(key_id: str, request: Request, principal: Revoker) -> Response:
    if not _KEY_ID.fullmatch(key_id):
        raise key_not_found()
    engine: AsyncEngine = request.app.state.engine
    now = request.app.state.clock()
    async with engine.begin() as conn:
        keys = ApiKeyRepository(conn, principal.tenant)
        found = await keys.get(key_id)
        if found is None or found.revoked_at is not None:
            raise key_not_found()
        await authorize_audited(request, principal, actions.API_KEY_REVOKE, found)
        await keys.revoke(key_id, now)
    await audit_allowed(
        request, principal, "api_key.revoke", resource_kind="api_key", resource_id=key_id
    )
    return Response(status_code=204)
