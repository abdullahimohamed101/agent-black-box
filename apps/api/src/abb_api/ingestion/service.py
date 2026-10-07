"""Ingestion use case: validate each event, bind it to the tenant, store, and report per event."""

import json
import logging
import re
from typing import Any

from abb_event_schema.errors import ErrorCode, EventValidationError
from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind
from abb_event_schema.parse import parse_event_in
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.clock import Clock
from abb_api.core.config import Settings
from abb_api.core.errors import (
    AppError,
    ErrorCategory,
    dependency_unavailable,
    is_connectivity_error,
)
from abb_api.core.request_context import get_request_id
from abb_api.ids import public_id
from abb_api.ingestion.body import batch_invalid
from abb_api.ingestion.ratelimit import RateLimiter, retry_after_seconds
from abb_api.ingestion.schemas import BatchResponse, EventErrorOut, IssueOut
from abb_api.ingestion.store import PgEventStore
from abb_api.tenancy import Principal

logger = logging.getLogger(__name__)

_BATCH_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_EVENT_ID_HINT = re.compile(r"evt_[0-9A-Z]{26}")


def project_key_required() -> AppError:
    return AppError(
        "PROJECT_KEY_REQUIRED",
        "Ingestion needs an API key bound to a project.",
        category=ErrorCategory.AUTHORIZATION,
        status_code=403,
    )


def rate_limited(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "This project is sending telemetry faster than its limit.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


class IngestionService:
    def __init__(
        self, engine: AsyncEngine, limiter: RateLimiter, settings: Settings, clock: Clock
    ) -> None:
        self._engine = engine
        self._limiter = limiter
        self._settings = settings
        self._clock = clock

    async def ingest_batch(self, principal: Principal, document: bytes) -> BatchResponse:
        if principal.project_id is None:
            raise project_key_required()
        batch_id, raw_events = self._parse_batch(document)

        wait = self._limiter.acquire(
            str(principal.project_id), events=len(raw_events), bytes_=len(document)
        )
        if wait is not None:
            raise rate_limited(wait)

        received_at = self._clock()
        workspace = public_id(IdKind.WORKSPACE, principal.workspace_id)
        project = public_id(IdKind.PROJECT, principal.project_id)

        errors: list[EventErrorOut] = []
        valid: list[tuple[int, Event]] = []
        for index, raw in enumerate(raw_events):
            try:
                event = finalize(
                    parse_event_in(raw),
                    workspace_id=workspace,
                    project_id=project,
                    received_at=received_at,
                )
            except EventValidationError as exc:
                errors.append(self._error_out(index, raw, exc))
            else:
                valid.append((index, event))

        accepted = duplicates = conflicts = 0
        if valid:
            try:
                async with self._engine.begin() as conn:
                    outcome = await PgEventStore(conn, principal.tenant).ingest(
                        [event for _, event in valid]
                    )
            except Exception as exc:
                if is_connectivity_error(exc) or _is_transient_conflict(exc):
                    logger.warning("ingest unavailable", extra={"error_type": type(exc).__name__})
                    raise dependency_unavailable() from exc
                raise
            for (index, _), result in zip(valid, outcome.results, strict=True):
                if result.status == "rejected":
                    errors.append(
                        EventErrorOut(
                            index=index, event_id=result.event_id, code=result.code or "REJECTED"
                        )
                    )
            accepted, duplicates, conflicts = (
                outcome.accepted,
                outcome.duplicates,
                outcome.conflicts,
            )
            if conflicts:
                logger.warning(
                    "conflicting duplicate events ignored", extra={"conflicts": conflicts}
                )

        errors.sort(key=lambda e: e.index)
        return BatchResponse(
            batch_id=batch_id,
            accepted=accepted,
            duplicates=duplicates,
            conflicts=conflicts,
            rejected=len(errors),
            errors=errors,
            server_time=received_at,
            request_id=get_request_id(),
        )

    def _parse_batch(self, document: bytes) -> tuple[str | None, list[Any]]:
        try:
            body = json.loads(document)
        except (ValueError, RecursionError):
            raise batch_invalid("The body is not valid JSON.") from None
        if not isinstance(body, dict):
            raise batch_invalid("The body must be a JSON object with an 'events' array.")
        events = body.get("events")
        if not isinstance(events, list) or not events:
            raise batch_invalid("'events' must be a non-empty array.")
        limit = self._settings.ingest_max_batch_events
        if len(events) > limit:
            raise batch_invalid(f"A batch holds at most {limit} events.", max_events=limit)
        batch_id = body.get("batch_id")
        if batch_id is not None and not (
            isinstance(batch_id, str) and _BATCH_ID.fullmatch(batch_id)
        ):
            raise batch_invalid("'batch_id' must be 1-64 characters of [A-Za-z0-9_.:-].")
        return batch_id, events

    @staticmethod
    def _error_out(index: int, raw: Any, exc: EventValidationError) -> EventErrorOut:
        candidate = raw.get("event_id") if isinstance(raw, dict) else None
        event_id = (
            candidate
            if isinstance(candidate, str) and _EVENT_ID_HINT.fullmatch(candidate)
            else None
        )
        return EventErrorOut(
            index=index,
            event_id=event_id,
            code=exc.code.value,
            issues=[IssueOut(loc=list(i.loc), code=i.code, message=i.message) for i in exc.issues],
        )


def _is_transient_conflict(exc: BaseException) -> bool:
    """Deadlock or serialization failure: the client should simply retry the batch."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return sqlstate in {"40P01", "40001"}


__all__ = ["ErrorCode", "IngestionService"]
