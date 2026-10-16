"""PostgreSQL command-line tools and connection details, without leaking a password (ADR-162).

The tools get host, port, user and database as arguments and the password only through the
`PGPASSWORD` environment variable of the child process, never on a command line (`ps`). Anything a
tool prints is scrubbed of the password and the full URL before it is shown or logged.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.backup.errors import EXIT_TOOL, EXIT_USAGE, BackupError

_SSL_MODES: Final = {
    "true": "require",
    "false": "disable",
    "require": "require",
    "disable": "disable",
    "prefer": "prefer",
    "verify-ca": "verify-ca",
    "verify-full": "verify-full",
}


@dataclass(frozen=True)
class Conn:
    host: str | None
    port: int | None
    user: str | None
    password: str | None
    database: str
    sslmode: str | None
    url: str  # the asyncpg URL for our own queries; never printed

    def args(self) -> list[str]:
        out: list[str] = []
        if self.host:
            out += ["--host", self.host]
        if self.port:
            out += ["--port", str(self.port)]
        if self.user:
            out += ["--username", self.user]
        return out

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        if self.password:
            env["PGPASSWORD"] = self.password
        if self.sslmode:
            env["PGSSLMODE"] = self.sslmode
        return env

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.url) if s]

    def engine(self) -> AsyncEngine:
        return create_async_engine(self.url, poolclass=NullPool)


def parse_url(url: str) -> Conn:
    try:
        parsed = make_url(url)
    except Exception:
        raise BackupError(EXIT_USAGE, "the database URL is not a valid URL") from None
    if not parsed.database or not parsed.drivername.startswith("postgresql"):
        raise BackupError(EXIT_USAGE, "the database URL must be postgresql://...@host/database")
    query = {k: str(v) for k, v in parsed.query.items()}
    raw_ssl = (query.get("sslmode") or query.get("ssl") or "").lower()
    asyncpg_url = parsed.set(drivername="postgresql+asyncpg")
    # libpq-only query keys would confuse asyncpg; the mode travels through PGSSLMODE instead.
    asyncpg_url = asyncpg_url.difference_update_query(["sslmode", "ssl"])
    if raw_ssl and raw_ssl in _SSL_MODES:
        asyncpg_url = asyncpg_url.update_query_dict({"ssl": _SSL_MODES[raw_ssl]})
    return Conn(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        database=parsed.database,
        sslmode=_SSL_MODES.get(raw_ssl),
        url=asyncpg_url.render_as_string(hide_password=False),
    )


def scrub(text: str, secrets: Sequence[str]) -> str:
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[redacted]")
    # A URL with credentials that is not ours (a tool echoing a connection string).
    return re.sub(r"(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+):[^@\s]+@", r"\1:[redacted]@", text)


def find_tool(name: str, environ: Mapping[str, str]) -> str:
    override = environ.get(f"ABB_{name.upper()}")
    found = override or shutil.which(name)
    if not found:
        raise BackupError(
            EXIT_TOOL,
            f"{name} is not installed (set ABB_{name.upper()} to its path; "
            "docs/runbooks/backup-restore.md)",
        )
    return found


def tool_version(path: str) -> tuple[int, str]:
    """(major, banner) of a PostgreSQL client tool."""
    try:
        out = subprocess.run(  # noqa: S603
            [path, "--version"], capture_output=True, text=True, timeout=30, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise BackupError(
            EXIT_TOOL, "could not run the PostgreSQL tool to read its version"
        ) from None
    match = re.search(r"\(PostgreSQL\)\s+(\d+)", out)
    if not match:
        raise BackupError(EXIT_TOOL, "could not read the PostgreSQL tool's version")
    return int(match.group(1)), out


def run_tool(argv: Sequence[str], conn: Conn, *, what: str) -> None:
    """Run a client tool, capturing stderr to a scrubbed one-line failure message."""
    with subprocess.Popen(  # noqa: S603  (fixed argv, no shell)
        list(argv),
        env=conn.env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        _, err = proc.communicate()
    if proc.returncode != 0:
        tail = scrub(err.strip().splitlines()[-1] if err.strip() else "no output", conn.secrets())
        raise BackupError(EXIT_TOOL, f"{what} failed: {tail[:300]}")
