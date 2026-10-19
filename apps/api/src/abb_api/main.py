"""Application factory (spec §103: modular monolith). Modules are added as phases land."""

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api import __version__
from abb_api.alerts.router import router as alerts_router
from abb_api.alerts.service import AlertsService
from abb_api.analytics.postgres import PostgresAnalyticsStore
from abb_api.analytics.router import router as analytics_router
from abb_api.analytics.service import AnalyticsService
from abb_api.approvals.router import router as approvals_router
from abb_api.approvals.service import ApprovalService
from abb_api.artifacts.router import router as artifacts_router
from abb_api.artifacts.service import ArtifactService
from abb_api.artifacts.store import ArtifactStore, LocalFsArtifactStore
from abb_api.audit.router import router as audit_router
from abb_api.auth.active_keys import ActiveKeyIds
from abb_api.auth.cookies import is_secure
from abb_api.auth.keys_router import router as api_keys_router
from abb_api.auth.login import LoginService
from abb_api.auth.oidc import OidcClient
from abb_api.auth.repository import ApiKeyLookup
from abb_api.auth.router import router as auth_router
from abb_api.auth.session_throttle import SessionThrottle
from abb_api.auth.sessions_router import router as sessions_router
from abb_api.auth.throttle import AuthThrottle
from abb_api.authz.audit import new_denial_limiter
from abb_api.clock import Clock, system_clock
from abb_api.comparison.router import router as comparison_router
from abb_api.comparison.service import ComparisonService
from abb_api.core.body_budget import BodyBudget, BodyBudgetMiddleware
from abb_api.core.config import Settings, get_settings
from abb_api.core.errors import install_error_handlers
from abb_api.core.logging import configure_logging
from abb_api.core.middleware import RequestContextMiddleware
from abb_api.cost.router import rebuild_router
from abb_api.cost.router import router as pricing_router
from abb_api.db import create_engine
from abb_api.db.breaker import DbBreaker
from abb_api.db.role_check import PrivilegedDatabaseRole, RoleMonitor, enforce_role
from abb_api.evaluations.router import router as evaluators_router
from abb_api.evaluations.run_router import router as run_evaluations_router
from abb_api.evaluations.runs import RunEvaluationService
from abb_api.evaluations.service import EvaluatorService
from abb_api.experiments.router import router as experiments_router
from abb_api.experiments.service import ExperimentService
from abb_api.findings.router import router as findings_router
from abb_api.findings.service import FindingsService
from abb_api.health.readiness import Readiness
from abb_api.health.router import router as health_router
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, RateLimiter
from abb_api.ingestion.router import router as ingestion_router
from abb_api.ingestion.service import IngestionService
from abb_api.limits import Scope
from abb_api.limits.factory import (
    build_counters,
    default_ingest_bucket,
    ingest_limiter,
    minute_limiter,
    per_minute_bucket,
)
from abb_api.obs import tracing
from abb_api.obs.access import MetricsAuth
from abb_api.obs.catalogue import DEFAULT, Observability
from abb_api.obs.http import MetricsMiddleware
from abb_api.obs.router import router as metrics_router
from abb_api.obs.scrape import ScrapeSampler
from abb_api.policies.api_service import PolicyApiService
from abb_api.policies.router import router as policies_router
from abb_api.quotas.router import router as quotas_router
from abb_api.quotas.service import QuotaApiService
from abb_api.quotas.settings import default_limits
from abb_api.retention.router import router as retention_router
from abb_api.retention.service import RetentionApiService
from abb_api.runs.router import router as runs_router
from abb_api.runs.service import RunService
from abb_api.security.api_service import SecurityApiService
from abb_api.security.router import router as security_router
from abb_api.security.settings import ReanalysisPolicy
from abb_api.streaming.hub import StreamHub
from abb_api.streaming.router import router as streams_router
from abb_api.streaming.service import StreamService
from abb_api.workspaces.members_router import router as members_router
from abb_api.workspaces.router import router as projects_router

# Which credentials may call an operation (documented, and checked by test_openapi).
_BEARER_ONLY = {
    ("post", "/v1/policy/decide"),
    ("get", "/v1/approvals/{approval_id}/wait"),
    ("post", "/v1/approvals/{approval_id}/consume"),
    ("delete", "/v1/approvals/{approval_id}"),
    ("post", "/v1/events"),
    ("post", "/v1/events/batch"),
    ("post", "/v1/runs"),
    ("put", "/v1/artifacts/{artifact_id}"),
}
_SESSION_ONLY = {
    ("get", "/v1/me"),
    ("post", "/v1/invitations/accept"),
    ("get", "/v1/auth/sessions"),
    ("post", "/v1/auth/sessions/revoke-others"),
    ("delete", "/v1/auth/sessions/{session_id}"),
}
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
                "Agent BlackBox ingestion and query API. SDKs authenticate with a project key: "
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


async def _load_active_key_ids(engine: AsyncEngine, clock: Clock, limit: int) -> list[str]:
    """One indexed, bounded read of the usable key ids (the throttle's refresh, ADR-153)."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('statement_timeout', '4000', true)"))
        return await ApiKeyLookup(conn).active_key_ids(clock(), limit)


async def _load_active_session_handles(engine: AsyncEngine, clock: Clock, limit: int) -> list[str]:
    """One bounded read of the usable sessions' hash prefixes (the session throttle's hint set)."""
    now = clock()
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('statement_timeout', '4000', true)"))
        rows = await conn.execute(
            text(
                "SELECT substr(token_hash, 1, 16) FROM sessions WHERE revoked_at IS NULL "
                "AND expires_at > :now AND idle_expires_at > :now LIMIT :limit"
            ),
            {"now": now, "limit": limit},
        )
        return [bytes(row[0]).hex() for row in rows]


def _library_telemetry_off() -> dict[str, Any]:
    """FastAPI (0.142+) has its own OpenTelemetry: it records ``url.path`` and ``url.query``, the
    original input of validation errors and exception messages, and exports to any
    ``OTEL_EXPORTER_OTLP_*`` collector it finds. None of that may leave this process (INV-5), and
    our spans are the only ones (ADR-170), so it is switched off whatever the environment says.
    Older FastAPI has no such parameter."""
    if "telemetry" not in inspect.signature(FastAPI.__init__).parameters:
        return {}
    return {"telemetry": dict(tracing.LIBRARY_INSTRUMENTATION_DISABLED)}


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock = system_clock,
    rate_limiter: RateLimiter | None = None,
    artifact_store: ArtifactStore | None = None,
    login_limiter: RateLimiter | None = None,
    oidc_transport: httpx.AsyncBaseTransport | None = None,
    observability: Observability = DEFAULT,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # Local guards (cheap, in front); the shared tier behind them is built per lifespan, once the
    # engine exists (ADR-152).
    local_ingest = rate_limiter or default_ingest_bucket(settings)

    login_bucket = login_limiter or InMemoryRateLimiter(
        events_per_second=settings.login_global_per_minute / 60,
        burst_events=settings.login_global_per_minute,
        bytes_per_second=1.0,
        burst_bytes=1,
    )
    oidc = _oidc_client(settings, clock, oidc_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        metrics = observability.catalogue
        tracing.start(settings, service="abb-api", metrics=metrics)  # off unless asked (ADR-170)
        breaker = DbBreaker(
            threshold=settings.db_breaker_threshold,
            open_seconds=settings.db_breaker_open_seconds,
            window_seconds=settings.db_breaker_window_seconds,
            enabled=settings.db_breaker_enabled,
            metrics=metrics,
        )
        app.state.db_breaker = breaker
        engine = create_engine(
            settings.database_url,
            pool_wait=metrics.postgres_pool_wait_seconds,
            statement_timeout_seconds=settings.db_statement_timeout_seconds,
            breaker=breaker,
        )
        app.state.engine = engine
        try:
            startup_reasons = await enforce_role(engine, settings, service="api")  # KI-031
        except PrivilegedDatabaseRole:
            await engine.dispose()
            raise
        role_monitor = RoleMonitor(
            engine, settings, service="api", initial=startup_reasons, metrics=metrics
        )
        app.state.role_monitor = role_monitor
        local_store = artifact_store is None  # an injected store is not a directory we probe
        app.state.readiness = Readiness(
            lambda: engine,
            settings,
            breaker=breaker,
            role=role_monitor,
            artifacts_dir=settings.artifact_dir if local_store else None,
            metrics=metrics,
        )
        role_monitor.start()
        counters = build_counters(engine, settings, metrics)
        app.state.shared_counters = counters
        ingest_guard = ingest_limiter(
            local_ingest,
            counters,
            settings,
            events=Scope.INGEST_EVENTS,
            bytes_=Scope.INGEST_BYTES,
        )
        artifact_guard = ingest_limiter(
            local_ingest,
            counters,
            settings,
            events=Scope.ARTIFACT_COUNT,
            bytes_=Scope.ARTIFACT_BYTES,
        )
        app.state.login_limiter = minute_limiter(
            counters,
            Scope.LOGIN_START,
            settings.login_global_per_minute,
            block_divisor=settings.limits_block_divisor,
            local=login_bucket,
        )
        app.state.session_limiter = minute_limiter(
            counters,
            Scope.SESSION_WRITE,
            settings.session_writes_per_minute,
            block_divisor=settings.limits_block_divisor,
        )
        active_keys = ActiveKeyIds(
            lambda limit: _load_active_key_ids(engine, clock, limit),
            refresh_seconds=settings.auth_fail_active_keys_refresh_seconds,
            cap=settings.auth_fail_active_keys_cap,
            metrics=metrics,
        )
        app.state.auth_throttle = AuthThrottle(
            settings, counters, engine, active_keys=active_keys, metrics=metrics
        )
        active_sessions = ActiveKeyIds(
            lambda limit: _load_active_session_handles(engine, clock, limit),
            refresh_seconds=settings.session_fail_active_refresh_seconds,
            cap=settings.session_fail_active_cap,
            metrics=metrics,
            size_gauge=metrics.session_active_handles,
            failure_counter=metrics.session_active_refresh_failures_total,
        )
        session_throttle = SessionThrottle(
            settings, counters, active=active_sessions, metrics=metrics
        )
        app.state.session_throttle = session_throttle
        app.state.ingestion = IngestionService(engine, ingest_guard, settings, clock)
        app.state.runs = RunService(
            engine,
            clock,
            default_limits(settings) if settings.quotas_enabled else None,
            int(settings.quota_lock_timeout_seconds * 1000),
        )
        app.state.findings = FindingsService(engine, app.state.runs)
        app.state.security = SecurityApiService(
            engine,
            ReanalysisPolicy(
                max_runs=settings.security_reanalyse_max_runs,
                max_age_days=settings.security_reanalyse_max_age_days,
                delay_seconds=settings.findings_delay_seconds,
                cooldown_seconds=settings.security_reanalyse_cooldown_seconds,
            ),
            clock,
        )
        app.state.quotas = QuotaApiService(
            engine, default_limits(settings), clock, enforced=settings.quotas_enabled
        )
        app.state.policies = PolicyApiService(
            engine,
            writes_per_minute=settings.policy_writes_per_minute,
            validations_per_minute=settings.policy_validations_per_minute,
            dry_runs_per_minute=settings.policy_dry_runs_per_minute,
            counters=counters,
            block_divisor=settings.limits_block_divisor,
        )
        app.state.alerts = AlertsService(engine, settings, clock, counters)
        app.state.retention = RetentionApiService(engine, settings, clock, counters)
        hub = StreamHub(settings.database_url)
        app.state.hub = hub
        app.state.approvals = ApprovalService(engine, settings, clock, hub, counters=counters)
        app.state.comparison = ComparisonService(engine, settings)
        app.state.experiments = ExperimentService(engine, settings)
        app.state.evaluators = EvaluatorService(engine, settings.judge_provider)
        app.state.run_evaluations = RunEvaluationService(
            engine,
            stale_seconds=settings.eval_stale_seconds,
            judge_provider=settings.judge_provider,
        )
        app.state.login = (
            LoginService(
                engine, oidc, settings, clock, on_session_created=session_throttle.session_created
            )
            if oidc
            else None
        )
        app.state.artifacts = ArtifactService(
            engine,
            artifact_store or LocalFsArtifactStore(settings.artifact_dir),
            artifact_guard,
            clock,
            max_bytes=settings.artifact_max_bytes,
            scan_max_bytes=settings.security_scan_artifact_max_bytes,
            scan_max_seconds=settings.security_scan_artifact_max_seconds,
            quota_defaults=default_limits(settings) if settings.quotas_enabled else None,
        )
        app.state.analytics = AnalyticsService(
            engine,
            PostgresAnalyticsStore(engine, timeout_seconds=settings.analytics_timeout_seconds),
            clock,
        )
        app.state.streams = StreamService(
            engine, hub, app.state.runs, settings, clock, metrics=metrics
        )
        hub.start()
        if settings.auth_fail_enabled:
            await active_keys.start()  # bounded; a failure leaves the set unloaded and retries
        if settings.session_fail_enabled:
            await active_sessions.start()
        try:
            yield
        finally:
            await role_monitor.stop()
            await active_sessions.stop()
            await active_keys.stop()
            if counters is not None:
                await counters.close()
            await hub.stop()
            await engine.dispose()
            await asyncio.to_thread(tracing.stop, settings.otel_shutdown_timeout_seconds)

    if settings.allow_dev_sessions:
        # Never log the flag's effect as a secret; do say loudly that it exists.
        logging.getLogger(__name__).warning(
            "ABB_ALLOW_DEV_SESSIONS is set: the CLI can mint sign-in sessions and seed adds a "
            "local owner. %s",
            "Development convenience only."
            if settings.dev_sessions_enabled
            else "It is ignored because ABB_ENVIRONMENT is not development or test.",
        )
    # The interactive docs and the schema route are a deliberate choice per environment (KI-023).
    # `python -m abb_api.openapi` and the web client's generator call `app.openapi()` directly, so
    # they work with the routes off.
    docs = settings.docs_enabled
    app = FastAPI(
        title="Agent BlackBox API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        **_library_telemetry_off(),
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.denial_limiter = new_denial_limiter(settings.audit_denials_per_minute)
    install_error_handlers(app)
    # Innermost (added first): a body refused for the byte budget (503) is still a CORS-readable,
    # request-ID'd response (ADR-155).
    app.state.body_budget = BodyBudget(
        settings.max_body_bytes_in_flight, metrics=observability.catalogue
    )
    app.add_middleware(
        BodyBudgetMiddleware,
        budget=app.state.body_budget,
        ceiling_bytes=settings.body_ceiling_bytes,
        read_timeout_seconds=settings.body_read_timeout_seconds,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # Added last so it is outermost: request IDs exist before CORS or handlers run.
    app.add_middleware(
        RequestContextMiddleware, hsts=is_secure(settings), docs_enabled=settings.docs_enabled
    )
    # Outermost of all: counts what the inner layers refuse as well (ADR-165).
    app.add_middleware(MetricsMiddleware, catalogue=observability.catalogue)
    app.state.observability = observability
    app.include_router(health_router)
    if settings.metrics_token is not None:  # without a token /metrics is a plain 404
        app.state.metrics_auth = MetricsAuth(settings.metrics_token)
        app.state.metrics_limiter = per_minute_bucket(settings.metrics_requests_per_minute)
        app.state.scrape_sampler = ScrapeSampler(
            lambda: getattr(app.state, "engine", None),
            observability.catalogue,
            retention_mode=settings.retention_mode,
        )
        app.include_router(metrics_router)
    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(ingestion_router)
    app.include_router(runs_router)
    app.include_router(findings_router)
    app.include_router(security_router)
    app.include_router(quotas_router)
    app.include_router(retention_router)
    app.include_router(policies_router)
    app.include_router(approvals_router)
    app.include_router(alerts_router)
    app.include_router(artifacts_router)
    app.include_router(streams_router)
    app.include_router(pricing_router)
    app.include_router(rebuild_router)
    app.include_router(analytics_router)
    app.include_router(projects_router)
    app.include_router(members_router)
    app.include_router(api_keys_router)
    app.include_router(audit_router)
    app.include_router(evaluators_router)
    app.include_router(run_evaluations_router)
    app.include_router(comparison_router)
    app.include_router(experiments_router)
    _install_openapi(app, settings)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn entrypoint: `uvicorn abb_api.main:app_from_env --factory`."""
    return create_app()
