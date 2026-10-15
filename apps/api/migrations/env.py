"""Alembic environment: async engine, URL from ABB_DATABASE_URL (or -x url=... for tests)."""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from abb_api.db.tables import metadata

config = context.config
target_metadata = metadata


def _database_url() -> str:
    # Migrations need DDL rights, the application does not (KI-020): a separate URL may be given.
    url = (
        context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("ABB_MIGRATION_DATABASE_URL")
        or os.environ.get("ABB_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("ABB_DATABASE_URL is not set (or pass -x url=...).")
    return url


def _set_role_password(connection: Connection, role: str, variable: str) -> None:
    """Give a login role a password from the environment (idempotent, every run).

    The password never lives in a migration file. Without the variable the role stays NOLOGIN, so
    a deployment cannot accidentally run with a well-known password.
    """
    command = getattr(getattr(config, "cmd_opts", None), "cmd", None)
    ran_upgrade = bool(command) and command[0].__name__ == "upgrade"
    at_head = context.get_context().get_current_revision() == context.get_head_revision()
    if not (ran_upgrade and at_head):
        return  # never touch a role on downgrades or read-only commands
    password = os.environ.get(variable)
    exists = connection.exec_driver_sql(
        f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"  # noqa: S608  (fixed role names)
    )
    if not password or exists.first() is None:
        return
    literal = "'" + password.replace("'", "''") + "'"
    connection.exec_driver_sql(f"ALTER ROLE {role} LOGIN PASSWORD {literal}")


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
        _set_role_password(connection, "abb_runtime", "ABB_RUNTIME_DB_PASSWORD")
        # The retention job's own role (ADR-159); a separate secret, never the runtime one.
        _set_role_password(connection, "abb_retention", "ABB_RETENTION_DB_PASSWORD")


async def _run_async() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
