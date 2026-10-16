"""Shared pieces of the backup and restore tests: scratch databases, the CLI runner, digests."""

import hashlib
import io
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.backup.cli import run
from tests.conftest import SERVER_URL, _admin, alembic

PASSPHRASE = "correct horse battery staple 19b"


def database_name() -> str:
    return f"abb_p19b_{uuid.uuid4().hex[:12]}"


def url_of(name: str) -> str:
    return make_url(SERVER_URL).set(database=name).render_as_string(hide_password=False)


@asynccontextmanager
async def scratch_database(*, migrate_to: str | None = None) -> AsyncIterator[str]:
    """A database of ours (abb_p19b_*): empty, or migrated to a revision. Dropped on exit."""
    name = database_name()
    await _admin(f'CREATE DATABASE "{name}"')
    url = url_of(name)
    try:
        if migrate_to:
            alembic(url, "upgrade", migrate_to)
        yield url
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def base_environ(**extra: str) -> dict[str, str]:
    """The process environment without ABB_* (no stray URLs or role passwords), plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ABB_", "PG"))}
    env.update(extra)
    return env


async def run_cli(*argv: str, environ: Mapping[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await run(list(argv), environ, out, err)
    return code, out.getvalue(), err.getvalue()


def write_passphrase(
    directory: Path, value: str = PASSPHRASE, mode: int = 0o600, name: str = "passphrase"
) -> Path:
    path = directory / name
    path.write_text(value + "\n")
    path.chmod(mode)
    return path


async def table_digest(engine: AsyncEngine, table: str, order: str) -> str:
    """A hash of every row of a table as JSON, in a fixed order: equal means equal content."""
    digest = hashlib.sha256()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY {order}")  # noqa: S608
        )
        for (row,) in result:
            digest.update(row.encode())
    return digest.hexdigest()


def _normal(acl: str | None) -> str | None:
    """An ACL that only gives the owner its own default rights is the same as no ACL (NULL)."""
    if acl and re.fullmatch(r"\{(\w+)=[a-zA-Z]+/\1\}", acl):
        return None
    return acl


async def privileges(engine: AsyncEngine) -> list[tuple[str, str | None]]:
    """Table, sequence and column privileges of the public schema (the grants the roles hold)."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, c.relacl::text FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S') "
                "UNION ALL SELECT c.relname || '.' || a.attname, a.attacl::text "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND a.attacl IS NOT NULL ORDER BY 1"
            )
        )
        return [(r[0], _normal(r[1])) for r in rows]


async def count(url: str, table: str) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608
    finally:
        await engine.dispose()


def read_json(path: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(path.read_text())  # type: ignore[no-any-return]
