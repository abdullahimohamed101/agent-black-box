"""A per-process cap on request-body bytes held in memory at once (Phase 19a, ADR-155, KI-026).

Routes that buffer a body (ingestion, artifacts, approvals, every JSON write) read it whole, so many
concurrent large requests multiply the per-request cap into memory pressure. This pure ASGI
middleware counts the body bytes the application has actually pulled from the socket and refuses
what would not fit with `503 BUSY` plus `Retry-After`:

- Before the body is read, when `Content-Length` says it cannot fit right now.
- While it is read, when concurrent requests grew past the cap after their own admission check
  (chunked bodies, or several requests admitted together).

Only bytes the handler really consumed are counted, so a request that is turned away before its
body is read (a bad key, a rate limit) costs nothing, and an anonymous client cannot reserve budget
by declaring a large `Content-Length` it never sends. Accounting is released in a `finally`, so
a client disconnect, a cancelled task or an exception cannot leak it. A body read is also bounded in
time, so a trickling client cannot hold bytes for ever.

Fairness: first come, first served. A request whose body is at most `SMALL_BODY_BYTES` is always
admitted (it is still counted), so sign-in, approval decisions and other small writes are not
starved by a flood of large batches; the bound is therefore the cap plus the small allowance of each
concurrent request. There is no per-project share: per-project fairness is the rate limiter's and
the quotas' job.
"""

import asyncio
import logging
import time

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from abb_api.core.errors import AppError, ErrorCategory, error_response

logger = logging.getLogger(__name__)

# Bodies up to this size are never refused for budget (still counted).
SMALL_BODY_BYTES = 64 * 1024
RETRY_AFTER_SECONDS = "2"
# Logged at most this often, so a flood cannot turn the log into the next problem.
_LOG_EVERY_SECONDS = 10.0


def busy_error() -> AppError:
    return AppError(
        "BUSY",
        "The server is busy receiving other large requests. Retry shortly.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )


class BodyBudget:
    """Bytes of request bodies held by this process. Single event loop: no lock is needed."""

    def __init__(self, cap_bytes: int, *, small_bytes: int = SMALL_BODY_BYTES) -> None:
        self.cap_bytes = cap_bytes
        self.small_bytes = small_bytes
        self.in_flight = 0
        self.peak = 0  # highest `in_flight` seen (observability and the benchmark)
        self.refused = 0
        self._last_log = float("-inf")

    def fits(self, want: int) -> bool:
        """Whether `want` bytes could be admitted now (the early, Content-Length check)."""
        return want <= self.small_bytes or self.in_flight + want <= self.cap_bytes

    def grow(self, held: int, want: int) -> bool:
        """Raise one request's accounted bytes from `held` to `want`; False (and nothing changed)
        when that would pass the cap."""
        delta = want - held
        if delta <= 0:
            return True
        if want > self.small_bytes and self.in_flight + delta > self.cap_bytes:
            return False
        self.in_flight += delta
        self.peak = max(self.peak, self.in_flight)
        return True

    def release(self, held: int) -> None:
        self.in_flight = max(0, self.in_flight - held)

    def note_refusal(self, where: str) -> None:
        self.refused += 1
        now = time.monotonic()
        if now - self._last_log >= _LOG_EVERY_SECONDS:
            self._last_log = now
            logger.warning(
                "request body refused: byte budget full",
                extra={"where": where, "in_flight": self.in_flight, "cap": self.cap_bytes},
            )


class BodyBudgetMiddleware:
    def __init__(
        self, app: ASGIApp, budget: BodyBudget, *, ceiling_bytes: int, read_timeout_seconds: float
    ) -> None:
        self.app = app
        self.budget = budget
        self.ceiling = ceiling_bytes
        self.read_timeout = read_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared_text = headers.get("content-length")
        chunked = "chunked" in headers.get("transfer-encoding", "").lower()
        declared = int(declared_text) if declared_text and declared_text.isdigit() else 0
        if declared == 0 and not chunked:
            await self.app(scope, receive, send)  # no body: nothing to account
            return
        if declared and not self.budget.fits(min(declared, self.ceiling)):
            self.budget.note_refusal("content-length")
            await error_response(busy_error())(scope, receive, send)
            return

        held = 0
        body_done = False
        deadline = time.monotonic() + self.read_timeout

        async def counted_receive() -> Message:
            nonlocal held, body_done
            if body_done:
                return await receive()
            try:
                message = await asyncio.wait_for(receive(), max(deadline - time.monotonic(), 0.001))
            except TimeoutError:
                raise AppError(
                    "REQUEST_TIMEOUT",
                    "The request body was not received in time.",
                    category=ErrorCategory.TIMEOUT,
                    status_code=408,
                    retryable=True,
                ) from None
            if message["type"] == "http.request":
                if not message.get("more_body", False):
                    body_done = True
                size = len(message.get("body", b""))
                if size:
                    want = held + size
                    if want > self.ceiling:
                        raise AppError(
                            "PAYLOAD_TOO_LARGE",
                            f"Request body exceeds the {self.ceiling} byte limit.",
                            category=ErrorCategory.VALIDATION,
                            status_code=413,
                            details={"limit_bytes": self.ceiling},
                        )
                    if not self.budget.grow(held, want):
                        self.budget.note_refusal("streaming")
                        raise busy_error()
                    held = want
            return message

        try:
            await self.app(scope, counted_receive, send)
        finally:
            self.budget.release(held)
