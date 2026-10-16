"""A per-process cap on request-body bytes held in memory at once (Phase 19a, ADR-155, KI-026).

Routes that buffer a body (ingestion, artifacts, approvals, every JSON write) read it whole, so many
concurrent large requests multiply the per-request cap into memory pressure. This pure ASGI
middleware accounts for the body bytes of every request and refuses what would not fit with
`503 BUSY` plus `Retry-After`:

- Before the body is read, when `Content-Length` says it cannot fit right now. The declared size
  (capped at the largest allowed body) is *reserved* at admission, so many requests declaring large
  bodies together cannot all pass and then be refused one by one after transferring bytes.
- While it is read, when concurrent requests grew past the cap (chunked bodies, which declare
  nothing and are counted as they arrive).

A reservation is reconciled against what really arrived: once the body is complete the unread
part is released, and it is also released when the read timeout passes without a complete body
(`ABB_BODY_READ_TIMEOUT_SECONDS`), so a client that declares a large body and never sends it holds
budget for at most that long (and no longer than its request lives). Peak accounted bytes never
exceed the cap plus the small allowance. The cost of this choice, accepted in ADR-155: a flood of
anonymous clients that declare large bodies and stall can make other *large* bodies wait with
`503 BUSY` for up to the read timeout; small bodies are never refused. Accounting is released in a
`finally`, so a disconnect, a cancelled task or an exception cannot leak it.

Fairness: first come, first served. A request whose body is at most `SMALL_BODY_BYTES` is always
admitted (it is still counted), so sign-in, approval decisions and other small writes are not
starved by a flood of large batches. There is no per-project share: per-project fairness is the
rate limiter's and the quotas' job.
"""

import asyncio
import logging
import time

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from abb_api.core.errors import AppError, ErrorCategory, error_response
from abb_api.obs.catalogue import METRICS, Catalogue

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

    def __init__(
        self,
        cap_bytes: int,
        *,
        small_bytes: int = SMALL_BODY_BYTES,
        metrics: Catalogue = METRICS,
    ) -> None:
        self.metrics = metrics
        # Read at scrape time: nothing is added to the accounting itself.
        metrics.body_bytes_in_flight.set_function(lambda: float(self.in_flight))
        self.cap_bytes = cap_bytes
        self.small_bytes = small_bytes
        self.in_flight = 0
        self.peak = 0  # highest `in_flight` seen (observability and the benchmark)
        self.refused = 0
        self._last_log = float("-inf")

    def fits(self, want: int) -> bool:
        """Whether `want` bytes could be admitted now (the early, Content-Length check)."""
        return want <= self.small_bytes or self.in_flight + want <= self.cap_bytes

    def reserve(self, want: int) -> bool:
        """Account for a declared body up front. False (nothing changed) when it does not fit."""
        return self.grow(0, want)

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
        self.metrics.body_cap_refusals_total.inc("budget")
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
        reserved = min(declared, self.ceiling)
        if reserved and not self.budget.reserve(reserved):
            self.budget.note_refusal("content-length")
            await error_response(busy_error())(scope, receive, send)
            return

        held = 0  # body bytes that really arrived
        accounted = reserved  # what the budget currently counts for this request
        body_done = False
        deadline = time.monotonic() + self.read_timeout

        def settle() -> None:
            """Give back the part of the reservation that has not arrived (and now never will)."""
            nonlocal accounted
            if accounted > held:
                self.budget.release(accounted - held)
                accounted = held

        # A reservation never outlives the read timeout, whether or not the handler is reading.
        expiry = (
            asyncio.get_running_loop().call_later(self.read_timeout, lambda: settle())
            if reserved
            else None
        )

        async def counted_receive() -> Message:
            nonlocal held, body_done, accounted
            if body_done:
                return await receive()
            try:
                message = await asyncio.wait_for(receive(), max(deadline - time.monotonic(), 0.001))
            except TimeoutError:
                self.budget.metrics.body_cap_refusals_total.inc("timeout")
                raise AppError(
                    "REQUEST_TIMEOUT",
                    "The request body was not received in time.",
                    category=ErrorCategory.TIMEOUT,
                    status_code=408,
                    retryable=True,
                ) from None
            if message["type"] == "http.request":
                size = len(message.get("body", b""))
                if size:
                    want = held + size
                    if want > self.ceiling:
                        self.budget.metrics.body_cap_refusals_total.inc("ceiling")
                        raise AppError(
                            "PAYLOAD_TOO_LARGE",
                            f"Request body exceeds the {self.ceiling} byte limit.",
                            category=ErrorCategory.VALIDATION,
                            status_code=413,
                            details={"limit_bytes": self.ceiling},
                        )
                    if want > accounted:
                        if not self.budget.grow(accounted, want):
                            self.budget.note_refusal("streaming")
                            raise busy_error()
                        accounted = want
                    held = want
                if not message.get("more_body", False):
                    body_done = True
                    settle()
            return message

        try:
            await self.app(scope, counted_receive, send)
        finally:
            if expiry is not None:
                expiry.cancel()
            self.budget.release(accounted)
