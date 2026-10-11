"""Evaluator use cases: validation against the built-in registry, bounds, withholding (D2, D7)."""

import json
import uuid
from typing import Any

from abb_event_schema.ids import IdKind
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.evaluations.builtin import REGISTRY, WITHHELD_CONFIG_FIELDS
from abb_api.evaluations.repository import EvaluatorRecord, EvaluatorRepository
from abb_api.evaluations.schemas import EvaluatorIn, EvaluatorOut, EvaluatorPage
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.projects.access import authorise_project

MAX_EVALUATORS = 100  # active, per workspace (D10)
MAX_CONFIG_BYTES = 16_384  # the CHECK on `evaluators.config` is the backstop for this


def not_found(code: str, what: str) -> AppError:
    return AppError(code, f"{what} not found.", category=ErrorCategory.NOT_FOUND, status_code=404)


def evaluator_not_found() -> AppError:
    return not_found("EVALUATOR_NOT_FOUND", "Evaluator")


def _invalid(code: str, message: str, **details: Any) -> AppError:
    return AppError(
        code, message, category=ErrorCategory.VALIDATION, status_code=422, details=details
    )


def _conflict(code: str, message: str, **details: Any) -> AppError:
    return AppError(
        code, message, category=ErrorCategory.CONFLICT, status_code=409, details=details
    )


def judge_not_configured() -> AppError:
    return _conflict(
        "JUDGE_NOT_CONFIGURED", "No model-judge provider is configured on this deployment."
    )


def jsonb_size(value: Any) -> int:
    """Bytes of `value` as PostgreSQL renders jsonb text (what `octet_length(col::text)` counts)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def validate_config(
    kind: str, implementation: str, config: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """The implementation's version and its normalised configuration, or a 4xx.

    Errors name fields and rule types only: a rejected value may be a path (Class 2 content) and
    must not be echoed.
    """
    if kind == "llm_judge":
        raise judge_not_configured()  # until a provider exists (step 7 replaces this)
    impl = REGISTRY.get(implementation)
    if impl is None:
        raise _invalid("EVALUATOR_INVALID", "Unknown evaluator implementation.")
    if impl.kind != kind:
        raise _invalid(
            "EVALUATOR_KIND_MISMATCH",
            "The implementation is of a different kind.",
            expected=impl.kind,
        )
    try:
        clean = impl.config_model.model_validate(config).model_dump(mode="json")
    except ValidationError as exc:
        problems = [
            {"loc": [str(p) for p in e["loc"]], "type": e["type"]}
            for e in exc.errors(include_input=False, include_url=False, include_context=False)
        ][:20]
        raise _invalid(
            "EVALUATOR_INVALID", "The evaluator configuration is invalid.", problems=problems
        ) from None
    if jsonb_size(clean) > MAX_CONFIG_BYTES:
        raise _invalid("EVALUATOR_INVALID", "The evaluator configuration is too large.")
    return int(impl.version), clean


def withhold_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    hidden = sorted(k for k in config if k in WITHHELD_CONFIG_FIELDS)
    return {k: (None if k in hidden else v) for k, v in config.items()}, hidden


def evaluator_out(record: EvaluatorRecord, principal: Principal) -> EvaluatorOut:
    config, hidden = (
        (dict(record.config), [])
        if holds(principal, actions.PAYLOAD_READ)
        else withhold_config(record.config)
    )
    return EvaluatorOut(
        id=public_id(IdKind.EVALUATOR, record.id),
        name=record.name,
        kind=record.kind,  # type: ignore[arg-type]
        implementation=record.implementation,
        version=record.version,
        config=config,
        config_withheld=hidden,
        description=record.description,
        project_id=public_id(IdKind.PROJECT, record.project_id) if record.project_id else None,
        ground_truth=record.kind != "llm_judge",
        created_at=record.created_at,
        archived_at=record.archived_at,
    )


class EvaluatorService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list(self, principal: Principal, *, include_archived: bool) -> EvaluatorPage:
        async with self._engine.connect() as conn:
            records = await EvaluatorRepository(conn, principal.tenant, principal.project_id).list(
                include_archived=include_archived, limit=MAX_EVALUATORS * 2
            )
        return EvaluatorPage(items=[evaluator_out(r, principal) for r in records])

    async def create(self, principal: Principal, body: EvaluatorIn) -> EvaluatorOut:
        version, config = validate_config(body.kind, body.implementation, body.config)
        async with self._engine.begin() as conn:
            project = await authorise_project(conn, principal, body.project_id)
            repo = EvaluatorRepository(conn, principal.tenant, principal.project_id)
            await repo.lock_for_create()
            if await repo.count_active() >= MAX_EVALUATORS:
                raise _conflict(
                    "LIMIT_REACHED",
                    f"A workspace holds at most {MAX_EVALUATORS} evaluators.",
                    limit="evaluators",
                )
            try:
                async with conn.begin_nested():
                    record = await repo.create(
                        new_uuid(IdKind.EVALUATOR),
                        project_id=project if body.project_id else None,
                        name=body.name,
                        kind=body.kind,
                        implementation=body.implementation,
                        version=version,
                        config=config,
                        description=body.description,
                        created_by=principal.user_id,
                    )
            except IntegrityError:
                raise _conflict(
                    "EVALUATOR_NAME_TAKEN", "An evaluator with this name already exists."
                ) from None
        return evaluator_out(record, principal)

    async def archive(self, principal: Principal, evaluator_id: str) -> EvaluatorRecord:
        parsed = parse_public_id(IdKind.EVALUATOR, evaluator_id)
        async with self._engine.begin() as conn:
            repo = EvaluatorRepository(conn, principal.tenant, principal.project_id)
            if parsed is None or not await repo.archive(parsed):
                raise evaluator_not_found()
            record = await repo.get(parsed)
        assert record is not None
        return record


def evaluator_uuid(evaluator_id: str) -> uuid.UUID:
    parsed = parse_public_id(IdKind.EVALUATOR, evaluator_id)
    if parsed is None:
        raise evaluator_not_found()
    return parsed
