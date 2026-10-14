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

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings

logger = logging.getLogger(__name__)

# One statement, evaluated for the role this connection is. `pg_has_role(.., 'USAGE')` also
# catches a login role that inherits an owning role.
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
        (SELECT pg_has_role(current_user, c.relowner, 'USAGE')
           FROM pg_class c WHERE c.oid = to_regclass('public.events')), false)
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


async def enforce_role(engine: AsyncEngine, settings: Settings, *, service: str) -> None:
    """Apply the policy; raises `PrivilegedDatabaseRole` only in production without the override."""
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
        return
    if not reasons:
        return
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
