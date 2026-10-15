"""Search errors. Positions and reasons depend on the query text only, never on stored data."""

from abb_api.core.errors import AppError, ErrorCategory


class QueryError(ValueError):
    """The query text is not valid under the grammar. `position` is a 0-based character offset."""

    def __init__(self, position: int, reason: str) -> None:
        super().__init__(reason)
        self.position = position
        self.reason = reason


def query_invalid(error: QueryError) -> AppError:
    return AppError(
        "QUERY_INVALID",
        "The search query is not valid.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"position": error.position, "reason": error.reason},
    )


def search_timeout() -> AppError:
    return AppError(
        "SEARCH_TIMEOUT",
        "The search took too long; narrow the time range or the project and try again.",
        category=ErrorCategory.TIMEOUT,
        status_code=503,
        retryable=True,
    )


def search_busy(reason: str) -> AppError:
    """`reason` is a fixed word (`actor_limit`, `workspace_share`, `process_full`), never data."""
    return AppError(
        "SEARCH_BUSY",
        "Too many searches are running; try again in a moment.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=503,
        retryable=True,
        headers={"Retry-After": "1"},
        details={"reason": reason},
    )
