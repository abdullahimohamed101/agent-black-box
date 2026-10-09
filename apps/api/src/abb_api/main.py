"""Application factory (spec §103: modular monolith). Modules are added as phases land."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from abb_api import __version__
from abb_api.analytics.postgres import PostgresAnalyticsStore
from abb_api.analytics.router import router as analytics_router
from abb_api.analytics.service import AnalyticsService
from abb_api.artifacts.router import router as artifacts_router
from abb_api.artifacts.service import ArtifactService
from abb_api.artifacts.store import ArtifactStore, LocalFsArtifactStore
from abb_api.audit.router import router as audit_router
from abb_api.auth.keys_router import router as api_keys_router
from abb_api.auth.login import LoginService
from abb_api.auth.oidc import OidcClient
from abb_api.auth.router import router as auth_router
from abb_api.authz.audit import new_denial_limiter
from abb_api.clock import Clock, system_clock
from abb_api.core.config import Settings, get_settings
from abb_api.core.errors import install_error_handlers
from abb_api.core.logging import configure_logging
from abb_api.core.middleware import RequestContextMiddleware
from abb_api.cost.router import rebuild_router
from abb_api.cost.router import router as pricing_router
from abb_api.db import create_engine
from abb_api.health.router import router as health_router
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, RateLimiter
from abb_api.ingestion.router import router as ingestion_router
from abb_api.ingestion.service import IngestionService
from abb_api.runs.router import router as runs_router
from abb_api.runs.service import RunService
from abb_api.streaming.hub import StreamHub
from abb_api.streaming.router import router as streams_router
from abb_api.streaming.service import StreamService
from abb_api.workspaces.members_router import router as members_router
from abb_api.workspaces.router import router as projects_router

# Which credentials may call an operation (documented, and checked by test_openapi).
_BEARER_ONLY = {
    ("post", "/v1/events"),
    ("post", "/v1/events/batch"),
    ("post", "/v1/runs"),
    ("put", "/v1/artifacts/{artifact_id}"),
}
_SESSION_ONLY = {("get", "/v1/me"), ("post", "/v1/invitations/accept")}
_NO_CREDENTIAL = {("get", "/v1/auth/login"), ("get", "/v1/auth/callback")}
_OPTIONAL_SESSION = {("post", "/v1/auth/logout")}
WORKSPACE_PARAMETER: dict[str, Any] = {
    "name": "X-ABB-Workspace",
    "in": "header",
    "required": False,
    "description": (
        "The workspace a signed-in user acts in (`ws_...`). Required with a session cookie on "
        "workspace routes; API keys are bound to their workspace and may omit it."
    ),
    "schema": {"type": "string"},
}


def _install_openapi(app: FastAPI, settings: Settings) -> None:
    """Document authentication on /v1 (it is parsed by hand, so FastAPI cannot infer it)."""
    from abb_api.auth.cookies import session_cookie_name

    def build() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=(
                "Agent Black Box ingestion and query API. SDKs authenticate with a project key: "
                "`Authorization: Bearer abb_live_<key_id>.<secret>`. The dashboard authenticates "
                "people with a session cookie and names its workspace in `X-ABB-Workspace`."
            ),
            routes=app.routes,
        )
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Project API key. Create one with `python -m abb_api.cli create-key`.",
        }
        schemes["sessionCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": session_cookie_name(settings),
            "description": "Browser session set by `GET /v1/auth/callback`.",
        }
        for path, operations in schema["paths"].items():
            if not path.startswith("/v1/"):
                continue
            for method, operation in operations.items():
                key = (method, path)
                if key in _NO_CREDENTIAL:
                    operation["security"] = []
                elif key in _OPTIONAL_SESSION:
                    operation["security"] = [{}, {"sessionCookie": []}]
                elif key in _SESSION_ONLY:
                    operation["security"] = [{"sessionCookie": []}]
                elif key in _BEARER_ONLY:
                    operation["security"] = [{"bearerAuth": []}]
                else:
                    operation["security"] = [{"bearerAuth": []}, {"sessionCookie": []}]
                    operation.setdefault("parameters", []).append(WORKSPACE_PARAMETER)
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]


def _oidc_client(
    settings: Settings, clock: Clock, transport: httpx.AsyncBaseTransport | None
) -> OidcClient | None:
    if settings.oidc_issuer is None:
        return None
    assert settings.oidc_client_id and settings.web_origin_normalised  # checked by Settings
    if settings.environment != "production":
        logging.getLogger(__name__).warning(
            "sign-in is configured but ABB_ENVIRONMENT is not production; development "
            "conveniences stay enabled. Set ABB_ENVIRONMENT=production when reachable.",
            extra={"environment": settings.environment},
        )
    secret = settings.oidc_client_secret
    return OidcClient(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=secret.get_secret_value() if secret else None,
        redirect_uri=f"{settings.web_origin_normalised}/api/auth/callback",
        clock=clock,
        extra_hosts=frozenset(h.strip() for h in settings.oidc_extra_hosts.split(",") if h.strip()),
        allow_http=settings.environment in ("development", "test"),
        transport=transport,
    )


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock = system_clock,
    rate_limiter: RateLimiter | None = None,
    artifact_store: ArtifactStore | None = None,
    login_limiter: RateLimiter | None = None,
    oidc_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    limiter = rate_limiter or InMemoryRateLimiter(
        events_per_second=settings.rate_limit_events_per_second,
        burst_events=settings.rate_limit_burst_events,
        bytes_per_second=settings.rate_limit_bytes_per_second,
        burst_bytes=settings.rate_limit_burst_bytes,
    )

    login_bucket = login_limiter or InMemoryRateLimiter(
        events_per_second=settings.login_global_per_minute / 60,
        burst_events=settings.login_global_per_minute,
        bytes_per_second=1.0,
        burst_bytes=1,
    )
    oidc = _oidc_client(settings, clock, oidc_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.ingestion = IngestionService(engine, limiter, settings, clock)
        app.state.runs = RunService(engine, clock)
        app.state.login = LoginService(engine, oidc, settings, clock) if oidc else None
        app.state.artifacts = ArtifactService(
            engine,
            artifact_store or LocalFsArtifactStore(settings.artifact_dir),
            limiter,
            clock,
            max_bytes=settings.artifact_max_bytes,
        )
        app.state.analytics = AnalyticsService(
            engine,
            PostgresAnalyticsStore(engine, timeout_seconds=settings.analytics_timeout_seconds),
            clock,
        )
        hub = StreamHub(settings.database_url)
        app.state.streams = StreamService(engine, hub, app.state.runs, settings)
        hub.start()
        try:
            yield
        finally:
            await hub.stop()
            await engine.dispose()

    app = FastAPI(title="Agent Black Box API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    app.state.login_limiter = login_bucket
    app.state.denial_limiter = new_denial_limiter(settings.audit_denials_per_minute)
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
    app.include_router(auth_router)
    app.include_router(ingestion_router)
    app.include_router(runs_router)
    app.include_router(artifacts_router)
    app.include_router(streams_router)
    app.include_router(pricing_router)
    app.include_router(rebuild_router)
    app.include_router(analytics_router)
    app.include_router(projects_router)
    app.include_router(members_router)
    app.include_router(api_keys_router)
    app.include_router(audit_router)
    _install_openapi(app, settings)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn entrypoint: `uvicorn abb_api.main:app_from_env --factory`."""
    return create_app()
