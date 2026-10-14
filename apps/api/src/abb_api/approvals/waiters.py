"""Bounds on concurrent long polls (ADR-141). Per API process, like the stream limits (KI-017)."""

import uuid
from collections import Counter

from abb_api.core.errors import AppError, ErrorCategory


def too_many_waiters(scope: str) -> AppError:
    return AppError(
        "TOO_MANY_WAITERS",
        f"Too many agents are waiting for approvals ({scope}). Retry shortly.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"scope": scope, "retry_after_seconds": 5},
        headers={"Retry-After": "5"},
    )


class WaiterGate:
    """Counts open waits. Used from one event loop, so no lock is needed."""

    def __init__(self, per_workspace: int, per_process: int) -> None:
        self._per_workspace = per_workspace
        self._per_process = per_process
        self._by_workspace: Counter[uuid.UUID] = Counter()
        self._total = 0

    @property
    def open_waits(self) -> int:
        return self._total

    def enter(self, workspace_id: uuid.UUID) -> None:
        if self._total >= self._per_process:
            raise too_many_waiters("server")
        if self._by_workspace[workspace_id] >= self._per_workspace:
            raise too_many_waiters("workspace")
        self._by_workspace[workspace_id] += 1
        self._total += 1

    def leave(self, workspace_id: uuid.UUID) -> None:
        if self._by_workspace[workspace_id] > 0:
            self._by_workspace[workspace_id] -= 1
            self._total -= 1
            if not self._by_workspace[workspace_id]:
                del self._by_workspace[workspace_id]
