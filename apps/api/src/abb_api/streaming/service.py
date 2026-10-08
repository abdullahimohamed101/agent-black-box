"""Live run streams: authorise, resume, then follow the run until it ends (ADR-022)."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, to_uuid
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings
from abb_api.core.errors import ErrorBody, ErrorCategory
from abb_api.core.request_context import get_request_id
from abb_api.ids import parse_public_id
from abb_api.runs.event_queries import EventQueries
from abb_api.runs.service import RunService, event_out
from abb_api.streaming import sse
from abb_api.streaming.hub import StreamHub
from abb_api.streaming.limits import StreamLease, StreamLimiter
from abb_api.streaming.resume import ArrivalCursor
from abb_api.tenancy import Principal

log = logging.getLogger("abb.streaming")

TERMINAL = frozenset({"run.completed", "run.failed", "run.cancelled"})
LIFECYCLE = (*sorted(TERMINAL), "run.started")


@dataclass(frozen=True)
class OpenStream:
    """An authorised stream, ready to be iterated. `lease` must be released when it ends."""

    principal: Principal
    run_id: uuid.UUID
    start: datetime | None
    lease: StreamLease


class StreamService:
    def __init__(
        self, engine: AsyncEngine, hub: StreamHub, runs: RunService, settings: Settings
    ) -> None:
        self._engine = engine
        self._hub = hub
        self._runs = runs
        self._settings = settings
        self.hub = hub
        self._db = asyncio.Semaphore(settings.stream_db_concurrency)
        self.limiter = StreamLimiter(settings.stream_max_total, settings.stream_max_per_key)

    async def open(
        self, principal: Principal, run_id: str, last_event_id: str | None
    ) -> OpenStream:
        """Raises the usual 404 (or 429) before any response bytes exist."""
        async with self._engine.connect() as conn:
            record = await self._runs.visible_run(conn, principal, run_id)
            start: datetime | None = None
            event_uuid = parse_public_id(IdKind.EVENT, last_event_id) if last_event_id else None
            if event_uuid is not None:  # an unknown or malformed id just means "from the start"
                start = await EventQueries(conn, principal.tenant).arrival_of(record.id, event_uuid)
        lease = self.limiter.acquire(principal.key_id)
        return OpenStream(principal, record.id, start, lease)

    async def frames(self, stream: OpenStream) -> AsyncIterator[bytes]:
        s = self._settings
        loop = asyncio.get_running_loop()
        cursor = ArrivalCursor(stream.start, timedelta(seconds=s.stream_overlap_seconds))
        key = (stream.principal.workspace_id, stream.run_id)
        deadline = loop.time() + s.stream_max_lifetime_seconds
        last_write = last_fresh = loop.time()
        subscription = self._hub.subscribe(key)
        try:
            yield sse.retry_frame()
            yield sse.comment("open")
            # A resume can start after the terminal event (outside the overlap): ask the database.
            terminal = await self._last_lifecycle(stream) in TERMINAL
            polled = loop.time() - s.stream_min_poll_seconds
            while True:
                # A floor between polls: a burst of NOTIFYs must not turn into a burst of queries.
                if (pause := s.stream_min_poll_seconds - (loop.time() - polled)) > 0:
                    await asyncio.sleep(pause)
                polled = loop.time()
                async for batch in self._fresh_pages(stream, cursor):
                    for event, has_payload in batch:
                        yield sse.frame(
                            "trace_event",
                            event_out(event, has_payload, with_payload=False).model_dump_json(),
                            event.event_id,
                        )
                        if event.event_type in TERMINAL:
                            terminal = True
                        elif event.event_type == "run.started":
                            terminal = False
                    last_write = last_fresh = loop.time()
                now = loop.time()
                if terminal and now - last_fresh >= s.stream_end_quiet_seconds:
                    yield sse.json_frame("run_end", {"reason": "run_finished"})
                    return
                if now >= deadline:
                    yield sse.comment("max-lifetime: reconnect with Last-Event-ID")
                    return
                if now - last_write >= s.stream_keepalive_seconds:
                    yield sse.comment("keepalive")
                    last_write = now
                wait = min(s.stream_fallback_poll_seconds, max(0.05, deadline - now))
                if terminal:
                    wait = min(wait, max(0.05, s.stream_end_quiet_seconds - (now - last_fresh)))
                await subscription.wait(wait)
        except (
            Exception
        ) as exc:  # the headers are sent: tell the client in-band, then let it reconnect
            log.warning("stream failed (%s)", type(exc).__name__)
            yield self._error_frame()
        finally:
            subscription.close()

    async def _last_lifecycle(self, stream: OpenStream) -> str | None:
        async with self._db, self._engine.connect() as conn:
            return await EventQueries(conn, stream.principal.tenant).last_lifecycle(
                stream.run_id, LIFECYCLE
            )

    async def _fresh_pages(
        self, stream: OpenStream, cursor: ArrivalCursor
    ) -> AsyncIterator[list[tuple[Event, bool]]]:
        """Rows this connection has not sent, page by page (queries borrow a connection briefly).

        Steady state is cheap: read strictly after the newest row sent (an index range scan), then
        compare the number of rows in the overlap window with what was sent. Only a mismatch, which
        means a row became visible late with an older arrival time, re-reads the window (ADR-022).
        """
        first = cursor.position is None
        async for batch in self._read(stream, cursor, since=cursor.lower_bound if first else None):
            yield batch
        position = cursor.position
        if first or position is None:
            return
        async with self._db, self._engine.connect() as conn:
            arrived = await EventQueries(conn, stream.principal.tenant).count_window(
                stream.run_id, since=cursor.lower_bound, upto=position
            )
        if arrived > cursor.window_size:
            async for batch in self._read(stream, cursor, since=cursor.lower_bound, resume=False):
                yield batch

    async def _read(
        self,
        stream: OpenStream,
        cursor: ArrivalCursor,
        *,
        since: datetime | None,
        resume: bool = True,
    ) -> AsyncIterator[list[tuple[Event, bool]]]:
        """Keyset pages after the cursor's position (`resume`) or from `since`, deduplicated."""
        limit = self._settings.stream_page_size
        after = cursor.position if resume else None
        while True:
            async with self._db, self._engine.connect() as conn:
                rows = await EventQueries(conn, stream.principal.tenant).arrived_since(
                    stream.run_id, since=since, after=after, limit=limit
                )
            fresh = cursor.unseen(rows)
            if fresh:
                yield fresh
            if len(rows) < limit:
                return
            last = rows[-1][0]
            after = (last.received_at, to_uuid(last.event_id))

    @staticmethod
    def _error_frame() -> bytes:
        body = ErrorBody(
            code="STREAM_UNAVAILABLE",
            message="The stream hit a temporary problem; reconnect with Last-Event-ID.",
            category=ErrorCategory.DEPENDENCY,
            retryable=True,
            request_id=get_request_id(),
            details={},
        )
        return sse.json_frame("error", {"error": body.model_dump(mode="json")})
