"""Ingestion use case: validate each event, bind it to the tenant, store, and report per event."""

import asyncio
import json
import logging
import re
from datetime import UTC, timedelta
from typing import Any

from abb_event_schema.errors import ErrorCode, EventValidationError, Issue
from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind
from abb_event_schema.parse import parse_event_in
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.config import Settings
from abb_api.core.errors import (
    AppError,
    ErrorCategory,
    dependency_unavailable,
    is_connectivity_error,
)
from abb_api.core.request_context import get_request_id, set_operation
from abb_api.ids import public_id
from abb_api.ingestion.body import batch_invalid
from abb_api.ingestion.emitter import (
    EMITTER_RESERVED_CODE,
    RESERVED_EVENT_TYPES,
    RESERVED_TYPE_CODE,
    SERVER_EMITTER,
)
from abb_api.ingestion.ratelimit import retry_after_seconds
from abb_api.ingestion.schemas import BatchResponse, EventErrorOut, IssueOut
from abb_api.ingestion.store import EventResult, PgEventStore, QuotaPolicy
from abb_api.limits import AsyncRateLimiter
from abb_api.obs.tracing import span
from abb_api.quotas.model import QUOTA_EXCEEDED, QUOTA_WORDS, Quota
from abb_api.quotas.settings import default_limits
from abb_api.security.redaction import (
    REASON,
    SCAN_FAILED,
    TOO_LARGE,
    RedactionError,
    ServerRedactor,
)
from abb_api.security.settings import CaptureMode, PgSecuritySettingsStore

logger = logging.getLogger(__name__)

# Below this many bytes a batch is redacted on the event loop (a few hundred microseconds);
# above it in a worker thread, so a large batch of text does not stall other requests.
_INLINE_REDACTION_BYTES = 32 * 1024

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
        self,
        engine: AsyncEngine,
        limiter: AsyncRateLimiter,
        settings: Settings,
        clock: Clock,
        redactor: ServerRedactor | None = None,
    ) -> None:
        self._engine = engine
        self._limiter = limiter
        self._settings = settings
        self._clock = clock
        self._redactor = redactor or ServerRedactor(
            event_max_seconds=settings.security_scan_event_max_seconds
        )
        self._summary_delay = timedelta(seconds=settings.summary_debounce_seconds)
        self._quota_defaults = default_limits(settings)

    async def ingest_batch(self, principal: Principal, document: bytes) -> BatchResponse:
        set_operation("ingest.batch")
        if principal.project_id is None:
            raise project_key_required()
        batch_id, raw_events = self._parse_batch(document)

        wait = await self._limiter.acquire(
            str(principal.project_id),
            events=len(raw_events),
            bytes_=len(document),
            workspace_id=principal.workspace_id,
        )
        if wait is not None:
            raise rate_limited(wait)

        received_at = self._clock()
        workspace = public_id(IdKind.WORKSPACE, principal.workspace_id)
        project = public_id(IdKind.PROJECT, principal.project_id)

        errors: list[EventErrorOut] = []
        valid: list[tuple[int, Event]] = []
        for index, raw in enumerate(raw_events):
            kind = raw.get("event_type") if isinstance(raw, dict) else None
            if isinstance(kind, str) and kind in RESERVED_EVENT_TYPES:
                # Decisions and approvals are the server's statements (ADR-141). Rejected for
                # this event only, like REDACTION_FAILED (the SDK does not retry it), and before
                # validation so the answer does not depend on which attributes the forger sent.
                errors.append(
                    EventErrorOut(
                        index=index,
                        event_id=self._event_id_hint(raw),
                        code=RESERVED_TYPE_CODE,
                        issues=[
                            IssueOut(
                                loc=["event_type"],
                                code="event_type_reserved",
                                message="This event type is written by the server.",
                            )
                        ],
                    )
                )
                continue
            try:
                client_event = parse_event_in(raw)
                if client_event.sdk is not None and client_event.sdk.name == SERVER_EMITTER:
                    raise EventValidationError(
                        ErrorCode.EVENT_INVALID,
                        "The emitter name is reserved for the server.",
                        [Issue(("sdk", "name"), EMITTER_RESERVED_CODE, "Reserved emitter name.")],
                    )
                event = finalize(
                    client_event,
                    workspace_id=workspace,
                    project_id=project,
                    received_at=received_at,
                )
            except EventValidationError as exc:
                errors.append(self._error_out(index, raw, exc))
            except Exception as exc:
                # A bug in validation must reject this one event, not 500 the batch: SDKs retry
                # server errors, so a single poison event would otherwise be resent forever.
                logger.error(
                    "event validation crashed",
                    extra={"error_type": type(exc).__name__},
                    exc_info=True,
                )
                errors.append(
                    EventErrorOut(
                        index=index,
                        event_id=self._event_id_hint(raw),
                        code="EVENT_INVALID",
                        issues=[
                            IssueOut(loc=[], code="validation_failed", message="Event rejected.")
                        ],
                    )
                )
            else:
                valid.append((index, event))

        accepted = duplicates = conflicts = 0
        if valid:
            valid = await self._redact(principal, valid, errors, size=len(document))
        if valid:
            try:
                quota = (
                    QuotaPolicy(
                        self._quota_defaults,
                        received_at.astimezone(UTC).date(),
                        int(self._settings.quota_lock_timeout_seconds * 1000),
                    )
                    if self._settings.quotas_enabled
                    else None
                )
                # Persistence including the commit (leaving `begin()`), and the job it enqueues.
                with span("ingest.persist", events=len(valid)) as persisted:
                    async with self._engine.begin() as conn:
                        outcome = await PgEventStore(
                            conn, principal.tenant, summary_delay=self._summary_delay, quota=quota
                        ).ingest([event for _, event in valid])
                    persisted.set(accepted=outcome.accepted)
            except Exception as exc:
                if is_connectivity_error(exc) or _is_transient_conflict(exc):
                    logger.warning("ingest unavailable", extra={"error_type": type(exc).__name__})
                    raise dependency_unavailable(exc) from exc
                raise
            for (index, _), result in zip(valid, outcome.results, strict=True):
                if result.status == "rejected":
                    errors.append(self._rejected_out(index, result))
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

    async def _redact(
        self,
        principal: Principal,
        valid: list[tuple[int, Event]],
        errors: list[EventErrorOut],
        *,
        size: int,
    ) -> list[tuple[int, Event]]:
        """Apply the project's capture mode and the secret scan before anything is stored.

        The mode is read for every batch (never cached): tightening a project must take effect
        at once. If it cannot be read nothing is stored (fail closed): a 503 the SDK retries.
        """
        assert principal.project_id is not None
        try:
            async with self._engine.connect() as conn:
                mode = (
                    await PgSecuritySettingsStore(conn, principal.tenant).get(principal.project_id)
                ).capture_mode
        except Exception as exc:
            if is_connectivity_error(exc):
                logger.warning("security settings unavailable")
                raise dependency_unavailable(exc) from exc
            raise
        if size <= _INLINE_REDACTION_BYTES:
            redacted, failed = self._redact_batch(valid, mode)
        else:
            redacted, failed = await asyncio.to_thread(self._redact_batch, valid, mode)
        for index, event in failed:
            errors.append(
                EventErrorOut(
                    index=index,
                    event_id=event.event_id,
                    code="REDACTION_FAILED",
                    issues=[IssueOut(loc=[], code="redaction_failed", message="Event rejected.")],
                )
            )
        original = {event.event_id: event for _, event in valid}
        changed = sum(1 for _, event in redacted if event is not original[event.event_id])
        if changed:
            # `scan_failed` (time budget, scanner error) is a capacity signal an operator watches;
            # `too_large` is the client's payload. Counts only, never a value.
            failures = [e.attributes.get(REASON) for _, e in redacted]
            logger.info(
                "server redaction applied",
                extra={
                    "events_changed": changed,
                    "scan_failed": failures.count(SCAN_FAILED),
                    "too_large": failures.count(TOO_LARGE),
                },
            )
        return redacted

    def _redact_batch(
        self, valid: list[tuple[int, Event]], mode: CaptureMode
    ) -> tuple[list[tuple[int, Event]], list[tuple[int, Event]]]:
        kept: list[tuple[int, Event]] = []
        failed: list[tuple[int, Event]] = []
        for index, event in valid:
            try:
                kept.append((index, self._redactor.apply(event, mode)))
            except RedactionError:
                failed.append((index, event))
            except Exception as exc:
                # Not stored as received: an unredacted event must never reach storage.
                logger.error("redaction crashed", extra={"error_type": type(exc).__name__})
                failed.append((index, event))
        return kept, failed

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
    def _rejected_out(index: int, result: EventResult) -> EventErrorOut:
        if result.quota is not None:
            # A closed vocabulary and fixed words: nothing the client sent is repeated.
            quota = Quota(result.quota)
            return EventErrorOut(
                index=index,
                event_id=result.event_id,
                code=QUOTA_EXCEEDED,
                quota=quota.value,
                issues=[
                    IssueOut(
                        loc=["quota"],
                        code=quota.value,
                        message=f"This project has reached its quota for {QUOTA_WORDS[quota]}.",
                    )
                ],
            )
        return EventErrorOut(index=index, event_id=result.event_id, code=result.code or "REJECTED")

    @staticmethod
    def _event_id_hint(raw: Any) -> str | None:
        """The event's id if it is readable and well formed (never echoes anything else)."""
        candidate = raw.get("event_id") if isinstance(raw, dict) else None
        ok = isinstance(candidate, str) and _EVENT_ID_HINT.fullmatch(candidate)
        return candidate if ok else None

    @staticmethod
    def _error_out(index: int, raw: Any, exc: EventValidationError) -> EventErrorOut:
        return EventErrorOut(
            index=index,
            event_id=IngestionService._event_id_hint(raw),
            code=exc.code.value,
            issues=[IssueOut(loc=list(i.loc), code=i.code, message=i.message) for i in exc.issues],
        )


def _is_transient_conflict(exc: BaseException) -> bool:
    """Deadlock, serialization failure or a lock wait that timed out (the project lock, ADR-154):
    the client should simply retry the batch."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return sqlstate in {"40P01", "40001", "55P03"}


__all__ = ["ErrorCode", "IngestionService"]
