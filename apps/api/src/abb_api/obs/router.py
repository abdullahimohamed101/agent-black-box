"""``GET /metrics`` (ADR-165). Registered only when ``ABB_METRICS_TOKEN`` is set.

Not a v1 tenant route: it carries no tenant data, takes a bearer token that is not an API key, and
is left out of the OpenAPI document. A wrong and a missing token get the same 401 body. Requests
are limited by one global bucket, checked first, so guessing is slow; the price is that a flood
of requests can make the scraper see 429 for a minute (documented trade-off).
"""

from fastapi import APIRouter, Request, Response

from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ingestion.ratelimit import retry_after_seconds
from abb_api.obs.metrics import CONTENT_TYPE

router = APIRouter()


def unauthorized() -> AppError:
    return AppError(
        "METRICS_TOKEN_INVALID",
        "The metrics token is missing or invalid.",
        category=ErrorCategory.AUTHENTICATION,
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _too_many(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "Too many requests to the metrics endpoint.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    state = request.app.state
    wait = state.metrics_limiter.acquire("metrics", events=1, bytes_=0)
    if wait is not None:
        raise _too_many(wait)
    if not state.metrics_auth.allows(request.headers.get("authorization")):
        raise unauthorized()
    return Response(state.observability.registry.render(), media_type=CONTENT_TYPE)
