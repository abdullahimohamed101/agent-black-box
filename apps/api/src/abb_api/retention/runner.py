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
from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.store import ArtifactStore, LocalFsArtifactStore
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
    # Where the artifact files live (the same directory the API writes). Enforcement removes
    # files through the ArtifactStore contract, so the process must see the same directory.
    artifact_dir: str = ".artifacts"
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
      -- Reading or forging a credential. DELETE alone is the purge's privilege (ADR-163): it can
      -- remove rows but neither read a secret nor mint a key.
      has_column_privilege(current_user, 'public.api_keys', 'secret_hash', 'SELECT,UPDATE')
        OR has_table_privilege(current_user, 'public.api_keys', 'INSERT')
        OR has_table_privilege(current_user, 'public.server_secrets', 'SELECT,INSERT,UPDATE,DELETE')
        OR has_column_privilege(
             current_user, 'public.alert_destinations', 'config_encrypted', 'SELECT,UPDATE')
        OR has_column_privilege(
             current_user, 'public.alert_destinations', 'signing_secret_encrypted',
             'SELECT,UPDATE')
        OR has_table_privilege(current_user, 'public.alert_destinations', 'INSERT')
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
        "halted": result.halted,
        "deleted_runs": result.deleted_runs,
        "expired_traces": result.expired_traces,
        "deleted_events": result.deleted_events,
        "failed": result.failed,
        "skipped_locked": result.skipped_locked,
    }


async def _engine(settings: RetentionRunnerSettings) -> AsyncEngine:
    engine = create_engine(settings.resolved_url())
    try:
        await enforce_retention_role(engine, settings)
    except BaseException:
        await engine.dispose()
        raise
    return engine


def artifact_store_of(settings: RetentionRunnerSettings) -> ArtifactStore | None:
    """The artifact store enforcement deletes through, or None unless the mode is `enforce`.

    A directory that does not exist is refused: unlinking a file that is not there succeeds, so a
    process pointed at the wrong place would delete every artifact ROW and leave the files behind.
    """
    if settings.retention_mode != "enforce":
        return None
    if not Path(settings.artifact_dir).is_dir():
        raise RetentionConfigError(
            "ABB_ARTIFACT_DIR is not an existing directory: enforcement would delete artifact "
            "records and leave the files. Mount the API's artifact directory."
        )
    return LocalFsArtifactStore(settings.artifact_dir)


async def mode_now() -> str:
    """The kill switch: the mode as configured NOW (the `.env` file is re-read; the process
    environment is not, so stop the process to change that). Anything unreadable means `off`."""
    try:
        return RetentionRunnerSettings().retention_mode
    except Exception:
        return "off"


async def run_once(settings: RetentionRunnerSettings) -> TickResult:
    if settings.retention_mode == "off":
        return TickResult(tick_id=uuid.uuid4(), mode="off")
    store = artifact_store_of(settings)
    engine = await _engine(settings)
    try:
        result = await run_tick(
            engine,
            settings,
            budget_seconds=settings.retention_tick_budget_seconds,
            keep_reports=settings.retention_reports_keep,
            store=store,
            mode_probe=mode_now,
        )
    finally:
        await engine.dispose()
    logger.info("retention tick finished", extra=_summary(result))
    return result


async def run_forever(settings: RetentionRunnerSettings, stop: asyncio.Event) -> None:
    """Tick every `retention_tick_interval_seconds` until told to stop (one tick at a time)."""
    if settings.retention_mode == "off":
        logger.info("ABB_RETENTION_MODE=off: the retention job does nothing")
    store = artifact_store_of(settings)
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
                        store=store,
                        mode_probe=mode_now,
                        stop=stop,
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


async def reset_failed(engine: AsyncEngine, max_attempts: int) -> int:
    """Give runs whose attempts ran out a fresh set (after the cause was fixed)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE retention_run_progress SET attempts = 0, failure_code = NULL, "
                "updated_at = now() WHERE attempts >= :max"
            ),
            {"max": max_attempts},
        )
        return int(result.rowcount)


async def retry_failed(settings: RetentionRunnerSettings) -> int:
    engine = await _engine(settings)
    try:
        return await reset_failed(engine, settings.retention_max_attempts)
    finally:
        await engine.dispose()


def purge_store_of(settings: RetentionRunnerSettings) -> ArtifactStore:
    """The artifact store a purge deletes through. Unlike enforcement it does not depend on the
    retention mode (a purge is an explicit action), but it has the same wrong-directory guard."""
    if not Path(settings.artifact_dir).is_dir():
        raise RetentionConfigError(
            "ABB_ARTIFACT_DIR is not an existing directory: a purge would delete artifact "
            "records and leave the files. Mount the API's artifact directory."
        )
    return LocalFsArtifactStore(settings.artifact_dir)


async def run_purge(settings: RetentionRunnerSettings, args: argparse.Namespace) -> int:
    """`purge-project` / `purge-workspace` (ADR-163): dry run unless `--confirm` names the slug."""
    from abb_api.retention import purge  # imported here: only the operator's process needs it

    scope = purge.WORKSPACE_SCOPE if args.command == "purge-workspace" else purge.PROJECT_SCOPE
    request = purge.PurgeRequest(
        scope=scope,
        workspace_slug=args.workspace,
        project_slug=getattr(args, "project", None),
        dry_run=args.dry_run,
        confirm=args.confirm,
    )
    deleting = not request.dry_run and request.confirm is not None
    store = purge_store_of(settings) if deleting else None
    engine = await _engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # stops at the next batch boundary
    try:
        return await purge.execute(
            engine,
            store,
            request,
            batch_size=settings.retention_batch_size,
            timeout_ms=max(int(settings.retention_statement_timeout_seconds * 1000), 1),
            stop=stop,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abb_api.retention", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="run one tick and print a summary (exit 2: refused or invalid)")
    sub.add_parser("run", help="tick repeatedly until stopped (the production process)")
    sub.add_parser(
        "retry-failed", help="give runs whose retry attempts ran out a fresh set of attempts"
    )
    for name, what in (("purge-project", "one project"), ("purge-workspace", "a whole workspace")):
        purge = sub.add_parser(
            name,
            help=f"PERMANENTLY delete {what} from every backend (dry run unless --confirm)",
        )
        purge.add_argument("--workspace", required=True, help="the workspace slug")
        if name == "purge-project":
            purge.add_argument("--project", required=True, help="the project slug")
        purge.add_argument(
            "--dry-run", action="store_true", help="print the counts and delete nothing (default)"
        )
        purge.add_argument(
            "--confirm",
            default=None,
            help="the exact slug of what is deleted (project slug, or workspace slug); "
            "without it nothing is deleted",
        )
    return parser


async def _run(args: argparse.Namespace, settings: RetentionRunnerSettings) -> int:
    command = args.command
    configure_logging(settings.log_level, service="abb-retention")
    if command == "run":
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_forever(settings, stop)
        return 0
    if command == "retry-failed":
        print(f"reset={await retry_failed(settings)}")
        return 0
    if command in ("purge-project", "purge-workspace"):
        return await run_purge(settings, args)
    result = await run_once(settings)
    summary = _summary(result)
    print(" ".join(f"{k}={v}" for k, v in summary.items()))
    return 2 if result.refused else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        settings = RetentionRunnerSettings()
        return asyncio.run(_run(args, settings))
    except RetentionConfigError as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
