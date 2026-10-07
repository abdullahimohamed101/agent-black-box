"""Application factory (spec §103: modular monolith). Modules are added as phases land."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from abb_api import __version__
from abb_api.core.config import Settings, get_settings
from abb_api.core.errors import install_error_handlers
from abb_api.core.logging import configure_logging
from abb_api.core.middleware import RequestContextMiddleware
from abb_api.db import create_engine, create_session_factory
from abb_api.health.router import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Agent Black Box API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
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
    return app


def app_from_env() -> FastAPI:
    """Uvicorn entrypoint: `uvicorn abb_api.main:app_from_env --factory`."""
    return create_app()
