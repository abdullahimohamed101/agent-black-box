"""Application factory (spec §103: modular monolith). Modules are added as phases land."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from abb_api import __version__
from abb_api.clock import Clock, system_clock
from abb_api.core.config import Settings, get_settings
from abb_api.core.errors import install_error_handlers
from abb_api.core.logging import configure_logging
from abb_api.core.middleware import RequestContextMiddleware
from abb_api.db import create_engine
from abb_api.health.router import router as health_router
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, RateLimiter
from abb_api.ingestion.router import router as ingestion_router
from abb_api.ingestion.service import IngestionService
from abb_api.runs.router import router as runs_router
from abb_api.runs.service import RunService


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock = system_clock,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    limiter = rate_limiter or InMemoryRateLimiter(
        events_per_second=settings.rate_limit_events_per_second,
        burst_events=settings.rate_limit_burst_events,
        bytes_per_second=settings.rate_limit_bytes_per_second,
        burst_bytes=settings.rate_limit_burst_bytes,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.ingestion = IngestionService(engine, limiter, settings, clock)
        app.state.runs = RunService(engine, clock)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Agent Black Box API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # Added last so it is outermost: request IDs exist before CORS or handlers run.
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(runs_router)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn entrypoint: `uvicorn abb_api.main:app_from_env --factory`."""
    return create_app()
