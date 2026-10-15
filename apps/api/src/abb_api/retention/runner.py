"""The retention job's process: `python -m abb_api.retention run|run-once` (ADR-159).

This is the only place that reads the retention role's credentials
(`ABB_RETENTION_DATABASE_URL`), and its settings class is separate from the API's `Settings`, so an
API process neither holds the credentials nor imports this module. Run it beside the worker, with
its own environment.
"""

import argparse
import asyncio
import logging
import signal
import sys
import uuid
from collections.abc import Sequence

from pydantic import Field
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import RetentionSettings
from abb_api.core.logging import configure_logging
from abb_api.db import create_engine
from abb_api.retention.tick import TickResult, run_tick

logger = logging.getLogger(__name__)


class RetentionConfigError(RuntimeError):
    """The runner cannot start safely; the message never contains a credential."""


class RetentionRunnerSettings(RetentionSettings):
    """Retention knobs plus the retention role's credentials. Never loaded by the API."""

    model_config = SettingsConfigDict(
        env_prefix="ABB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    log_level: str = "INFO"
    retention_database_url: str | None = None
    # Development and test only: the owner URL the migrations use, when no retention URL is set.
    migration_database_url: str | None = None
    database_url: str | None = None
    allow_privileged_db_role: bool = False
    retention_tick_interval_seconds: float = Field(default=3600.0, ge=1, le=7 * 24 * 3600)
    retention_tick_budget_seconds: float = Field(default=60.0, gt=0, le=3600)
    # Reports kept per workspace and tier (older ones are removed by the tick that writes the next).
    retention_reports_keep: int = Field(default=50, ge=1, le=10_000)

    @property
    def local(self) -> bool:
        return self.environment in ("development", "test")

    def resolved_url(self) -> str:
        """The retention role's URL. Never the application's runtime URL in production: there the
        explicit setting is required. Local development and tests may reuse the owner URL the
        migrations use (the owner can do everything the role can)."""
        if self.retention_database_url:
            return self.retention_database_url
        if self.local:
            owner = self.migration_database_url or self.database_url
            if owner:
                logger.warning(
                    "ABB_RETENTION_DATABASE_URL is not set: using the migration/owner URL "
                    "(local development only)"
                )
                return owner
        raise RetentionConfigError(
            "ABB_RETENTION_DATABASE_URL is required (the abb_retention role's URL; "
            "see docs/OPERATIONS.md, 'Data retention')"
        )


# Why a connected role is too privileged to run the job (closed set; messages carry no names).
_QUESTION = text(
    """
    SELECT
      r.rolsuper,
      r.rolbypassrls,
      COALESCE((SELECT bool_or(pg_has_role(current_user, c.relowner, 'USAGE'))
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')), false),
      has_schema_privilege(current_user, 'public', 'CREATE'),
      has_table_privilege(current_user, 'public.api_keys', 'SELECT,INSERT,UPDATE,DELETE')
        OR has_table_privilege(current_user, 'public.server_secrets', 'SELECT,INSERT,UPDATE,DELETE')
        OR has_table_privilege(
             current_user, 'public.alert_destinations', 'SELECT,INSERT,UPDATE,DELETE')
    FROM pg_roles r WHERE r.rolname = current_user
    """
)
ROLE_REASONS = ("superuser", "bypass_rls", "table_owner", "schema_create", "credential_tables")


async def privileges_of_retention_role(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        row = (await conn.execute(_QUESTION)).one()
    return [reason for reason, flag in zip(ROLE_REASONS, row, strict=True) if flag]


async def enforce_retention_role(engine: AsyncEngine, settings: RetentionRunnerSettings) -> None:
    """Production refuses a role that is more than the retention role; elsewhere it warns."""
    reasons = await privileges_of_retention_role(engine)
    if not reasons:
        return
    extra = {"reasons": reasons, "environment": settings.environment}
    if settings.local or settings.allow_privileged_db_role:
        logger.warning(
            "the retention process runs with a more privileged database role", extra=extra
        )
        return
    logger.error("the retention database role is too privileged", extra=extra)
    raise RetentionConfigError(
        "Refusing to start: the retention URL connects as a role that can do more than the "
        "abb_retention role (see the logged reasons). Use the abb_retention role, or set "
        "ABB_ALLOW_PRIVILEGED_DB_ROLE=1 to accept this knowingly."
    )


def _summary(result: TickResult) -> dict[str, object]:
    return {
        "mode": result.mode,
        "tick_id": str(result.tick_id),
        "workspaces": result.workspaces,
        "reports": result.reports,
        "truncated": result.truncated,
        "errors": result.errors,
        "budget_exhausted": result.budget_exhausted,
        "locked_out": result.locked_out,
        "refused": result.refused,
    }


async def _engine(settings: RetentionRunnerSettings) -> AsyncEngine:
    engine = create_engine(settings.resolved_url())
    try:
        await enforce_retention_role(engine, settings)
    except BaseException:
        await engine.dispose()
        raise
    return engine


async def run_once(settings: RetentionRunnerSettings) -> TickResult:
    if settings.retention_mode == "off":
        return TickResult(tick_id=uuid.uuid4(), mode="off")
    engine = await _engine(settings)
    try:
        result = await run_tick(
            engine,
            settings,
            budget_seconds=settings.retention_tick_budget_seconds,
            keep_reports=settings.retention_reports_keep,
        )
    finally:
        await engine.dispose()
    logger.info("retention tick finished", extra=_summary(result))
    return result


async def run_forever(settings: RetentionRunnerSettings, stop: asyncio.Event) -> None:
    """Tick every `retention_tick_interval_seconds` until told to stop (one tick at a time)."""
    if settings.retention_mode == "off":
        logger.info("ABB_RETENTION_MODE=off: the retention job does nothing")
    engine = await _engine(settings) if settings.retention_mode != "off" else None
    try:
        while not stop.is_set():
            if engine is not None:
                try:
                    result = await run_tick(
                        engine,
                        settings,
                        budget_seconds=settings.retention_tick_budget_seconds,
                        keep_reports=settings.retention_reports_keep,
                    )
                    logger.info("retention tick finished", extra=_summary(result))
                except Exception:  # e.g. the database restarted: try again next interval
                    logger.exception("retention tick failed")
            try:
                await asyncio.wait_for(stop.wait(), settings.retention_tick_interval_seconds)
            except TimeoutError:
                pass
    finally:
        if engine is not None:
            await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abb_api.retention", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="run one tick and print a summary (exit 2: refused or invalid)")
    sub.add_parser("run", help="tick repeatedly until stopped (the production process)")
    return parser


async def _run(command: str, settings: RetentionRunnerSettings) -> int:
    configure_logging(settings.log_level, service="abb-retention")
    if command == "run":
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_forever(settings, stop)
        return 0
    result = await run_once(settings)
    summary = _summary(result)
    print(" ".join(f"{k}={v}" for k, v in summary.items()))
    return 2 if result.refused else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        settings = RetentionRunnerSettings()
        return asyncio.run(_run(args.command, settings))
    except RetentionConfigError as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
