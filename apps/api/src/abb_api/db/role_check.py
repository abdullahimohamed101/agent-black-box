"""Startup check: serving processes must not connect as a database role that can rewrite events.

INV-1 (accepted events are immutable) is enforced by the database for the least-privilege runtime
role (`abb_runtime`, migration 0007): it can append to `events` but never UPDATE, DELETE or
TRUNCATE them, and it cannot change the schema. That separation is configuration (KI-020), so a
deployment that points the API or worker at the owner or a superuser silently loses the protection
(KI-031). This module asks the connected role what it can do and the serving processes act on the
answer: refuse to start in production, warn elsewhere, or log an audited override.

The reasons are a closed set and the messages are fixed strings: they never contain a role name,
host, password or connection string.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings
from abb_api.obs.catalogue import METRICS, Catalogue

logger = logging.getLogger(__name__)

# One statement, evaluated for the role this connection is. `pg_has_role(.., 'USAGE')` also
# catches a login role that inherits an owning role. Owning *any* table of the schema counts, not
# only `events`: an owner can grant itself everything on that table and alter its triggers.
_QUESTION = text(
    """
    SELECT
      r.rolsuper                                         AS superuser,
      r.rolbypassrls                                     AS bypass_rls,
      has_table_privilege(current_user, 'public.events', 'UPDATE')
        OR has_table_privilege(current_user, 'public.events', 'DELETE')
        OR has_table_privilege(current_user, 'public.events', 'TRUNCATE')
                                                         AS events_rewritable,
      COALESCE(
        (SELECT bool_or(pg_has_role(current_user, c.relowner, 'USAGE'))
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')), false)
                                                         AS events_owner,
      has_schema_privilege(current_user, 'public', 'CREATE')
                                                         AS schema_create
    FROM pg_roles r WHERE r.rolname = current_user
    """
)

# The only strings that can appear in a message or log line.
REASONS = ("superuser", "bypass_rls", "events_rewritable", "events_owner", "schema_create")

REFUSAL = (
    "Refusing to start: the database role this process connects as can rewrite or delete stored "
    "events or change the schema (see the logged reasons). Connect the API and worker as the "
    "least-privilege runtime role and run migrations as the owner (docs/OPERATIONS.md, "
    "'Database roles'), or set ABB_ALLOW_PRIVILEGED_DB_ROLE=1 to accept this knowingly."
)
UNVERIFIED_REFUSAL = (
    "Refusing to start: the privileges of the database role could not be verified at startup "
    "(is the database reachable?). Production does not run unverified; retry once it answers, or "
    "set ABB_ALLOW_PRIVILEGED_DB_ROLE=1 to skip the check knowingly."
)


class PrivilegedDatabaseRole(RuntimeError):
    """Production serving process connected as a role that can alter events."""


async def privileges_of_connected_role(engine: AsyncEngine) -> list[str]:
    """The reasons the connected role is too privileged (empty: it is a least-privilege role)."""
    async with engine.connect() as conn:
        row = (await conn.execute(_QUESTION)).one()
    return [reason for reason, flag in zip(REASONS, (bool(v) for v in row), strict=True) if flag]


async def enforce_role(
    engine: AsyncEngine, settings: Settings, *, service: str
) -> list[str] | None:
    """Apply the policy; raises `PrivilegedDatabaseRole` only in production without the override.

    Returns the reasons found (empty: clean), or None when the role could not be verified.
    """
    production = settings.environment == "production"
    try:
        reasons = await privileges_of_connected_role(engine)
    except Exception as error:  # the database is unreachable or the question failed
        logger.warning(
            "database role check could not run",
            extra={"component": service, "error_type": type(error).__name__},
        )
        if production and not settings.allow_privileged_db_role:
            raise PrivilegedDatabaseRole(UNVERIFIED_REFUSAL) from None
        return None
    if not reasons:
        return reasons
    extra = {"component": service, "reasons": reasons, "environment": settings.environment}
    if settings.allow_privileged_db_role:
        logger.warning(
            "ABB_ALLOW_PRIVILEGED_DB_ROLE is set: running with a database role that can rewrite "
            "events (INV-1 is not enforced by the database)",
            extra=extra,
        )
    elif production:
        logger.error("database role is too privileged", extra=extra)
        raise PrivilegedDatabaseRole(REFUSAL)
    else:
        logger.warning(
            "the database role can rewrite events (fine for local development, refused in "
            "production); use the abb_runtime role to mirror production",
            extra=extra,
        )
    return reasons


RoleState = Literal["ok", "privileged", "unverified"]
CHECK_TIMEOUT_SECONDS = 5.0


class RoleMonitor:
    """The role check repeated while the process runs (KI-183).

    A role granted more privilege after the process started (a mistaken GRANT, a changed
    membership) is noticed within one interval. The monitor never stops the process: in
    production a privileged role fails the readiness check `role` (so the orchestrator stops
    sending traffic and an operator is paged by the existing readiness alerts); in other
    environments it warns once per change. Each run is single-flight and bounded by a timeout, a
    run that cannot complete keeps the previous finding (the database check reports the outage),
    and only the fixed reason names are ever logged or exported.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        service: str,
        initial: list[str] | None = None,
        metrics: Catalogue = METRICS,
        sleep: Callable[[float], asyncio.Future[None]] | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._service = service
        self._metrics = metrics
        self._sleep = sleep or asyncio.sleep
        self._reasons: list[str] | None = initial
        # What was last logged; the startup check already logged `initial`.
        self._warned: tuple[str, ...] | None = tuple(initial) if initial else None
        self._flight: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self.runs = 0  # completed checks, for tests
        if initial is not None:
            self._publish(initial)

    @property
    def reasons(self) -> list[str] | None:
        return None if self._reasons is None else list(self._reasons)

    @property
    def state(self) -> RoleState:
        if self._reasons is None:
            return "unverified"
        return "privileged" if self._reasons else "ok"

    @property
    def refuses_traffic(self) -> bool:
        """Readiness fails on a privileged role in production unless the operator accepted it."""
        return (
            self.state == "privileged"
            and self._settings.environment == "production"
            and not self._settings.allow_privileged_db_role
        )

    def _publish(self, reasons: list[str]) -> None:
        self._metrics.db_role_privileged.replace({(r,): 1.0 for r in reasons})

    async def check_once(self) -> RoleState:
        """One check; concurrent callers share the run in flight."""
        flight = self._flight
        if flight is None or flight.done():
            flight = self._flight = asyncio.create_task(self._run_once(), name="abb-role-check")
        await asyncio.shield(flight)
        return self.state

    async def _run_once(self) -> None:
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
                found = await privileges_of_connected_role(self._engine)
        except Exception as error:  # keep the previous finding; say what failed, not why
            logger.warning(
                "periodic database role check could not run",
                extra={"component": self._service, "error_type": type(error).__name__},
            )
            return
        self.runs += 1
        self._reasons = found
        self._publish(found)
        key = tuple(found)
        if key == self._warned or (not found and self._warned is None):
            return
        self._warned = key if found else None
        if not found:
            logger.info(
                "database role is least-privilege again", extra={"component": self._service}
            )
            return
        extra = {"component": self._service, "reasons": found}
        if self.refuses_traffic:
            logger.error("database role became too privileged; /readyz now fails", extra=extra)
        else:
            logger.warning(
                "the database role can rewrite events (fine for local development, refused in "
                "production)",
                extra=extra,
            )

    async def _loop(self) -> None:
        interval = self._settings.role_check_interval_seconds
        while True:
            await self._sleep(interval)
            await self.check_once()

    def start(self) -> None:
        if self._settings.role_check_interval_seconds > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="abb-role-monitor")

    async def stop(self) -> None:
        task, self._task = self._task, None
        flight, self._flight = self._flight, None
        for pending in (task, flight):
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending
