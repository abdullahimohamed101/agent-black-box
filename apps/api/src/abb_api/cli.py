"""Provisioning CLI: workspaces, projects and API keys (dashboard sign-up arrives in Phase 15).

    python -m abb_api.cli create-workspace --name Acme --slug acme
    python -m abb_api.cli create-project --workspace acme --name Agent --slug coding-agent
    python -m abb_api.cli create-key --workspace acme --project coding-agent \
        --scopes events:write runs:read
    python -m abb_api.cli revoke-key --workspace acme --key-id <key_id>
    python -m abb_api.cli jobs-list --status dead_letter
    python -m abb_api.cli jobs-retry [--id <job uuid>]   # after fixing the cause
    python -m abb_api.cli set-pricing-override --workspace acme --model-pattern 'my-model*' \
        --input-per-million 3 --output-per-million 15 [--project p] [--valid-from 2026-10-01]
    python -m abb_api.cli list-pricing --workspace acme
    python -m abb_api.cli rebuild-costs --workspace acme [--project p] [--since 2026-10-01]
    python -m abb_api.cli seed            # local development only

A new key's secret is written to stdout exactly once; everything else goes to stderr so the key can
be captured with `$(...)`. Secrets are never logged.
"""

import argparse
import asyncio
import os
import stat
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth import scopes as scope_names
from abb_api.auth.keys import parse_key
from abb_api.auth.repository import ApiKeyRepository
from abb_api.auth.service import authenticate
from abb_api.clock import Clock, system_clock
from abb_api.core.config import Settings, get_settings
from abb_api.core.domain import AlreadyExistsError, DomainError, NotFoundError
from abb_api.core.errors import AppError
from abb_api.cost.builtin import BUILTIN_ENTRIES
from abb_api.cost.repository import CostRepository
from abb_api.db import create_engine
from abb_api.jobs.outbox import JobQueue, OutboxRepository
from abb_api.projects.repository import ProjectRepository
from abb_api.runs.repository import RunRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces import Workspace, WorkspaceProvisioning

SEED_WORKSPACE = ("Local development", "local")
SEED_PROJECT = ("Demo", "demo")
SEED_KEY_NAME = "dev-seed"
DEFAULT_KEY_FILE = Path(".local/dev-api-key")


def _price(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not value.is_finite() or value < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return value


def _instant(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date or time: {text!r}") from None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abb_api.cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    workspace = sub.add_parser("create-workspace")
    workspace.add_argument("--name", required=True)
    workspace.add_argument("--slug", required=True)

    project = sub.add_parser("create-project")
    project.add_argument("--workspace", required=True, help="workspace slug")
    project.add_argument("--name", required=True)
    project.add_argument("--slug", required=True)

    key = sub.add_parser("create-key")
    key.add_argument("--workspace", required=True, help="workspace slug")
    key.add_argument("--project", help="project slug; omit for a workspace-wide read key")
    key.add_argument(
        "--scopes",
        nargs="+",
        default=[scope_names.EVENTS_WRITE],
        help="e.g. events:write runs:read",
    )
    key.add_argument("--name")
    key.add_argument("--expires-in-days", type=int)

    revoke = sub.add_parser("revoke-key")
    revoke.add_argument("--workspace", required=True, help="workspace slug")
    revoke.add_argument("--key-id", required=True)

    jobs_list = sub.add_parser("jobs-list", help="show background jobs (default: dead letters)")
    jobs_list.add_argument(
        "--status", default="dead_letter", choices=["pending", "running", "done", "dead_letter"]
    )
    jobs_list.add_argument("--limit", type=int, default=50)

    jobs_retry = sub.add_parser(
        "jobs-retry", help="give dead-lettered jobs a fresh set of attempts"
    )
    jobs_retry.add_argument("--id", type=uuid.UUID, help="a single job; default: all dead letters")

    override = sub.add_parser(
        "set-pricing-override",
        help="add a price for a model (newer rows win); run rebuild-costs to apply to past runs",
    )
    override.add_argument("--workspace", required=True, help="workspace slug")
    override.add_argument("--project", help="project slug; omit for the whole workspace")
    override.add_argument("--provider", help="match only this provider (default: any)")
    override.add_argument("--model-pattern", required=True, help="glob, e.g. 'my-model*'")
    override.add_argument("--input-per-million", type=_price, required=True)
    override.add_argument("--output-per-million", type=_price, required=True)
    override.add_argument("--cached-input-per-million", type=_price)
    override.add_argument("--request-price", type=_price, default=Decimal(0))
    override.add_argument(
        "--valid-from", type=_instant, default=datetime(1970, 1, 1, tzinfo=UTC),
        help="ISO date or time (UTC); default: always",
    )  # fmt: skip
    override.add_argument("--note")

    pricing = sub.add_parser("list-pricing", help="show the built-in price table and overrides")
    pricing.add_argument("--workspace", required=True, help="workspace slug")

    rebuild = sub.add_parser(
        "rebuild-costs", help="re-derive runs (summary and cost lines) after a price change"
    )
    rebuild.add_argument("--workspace", required=True, help="workspace slug")
    rebuild.add_argument("--project", help="project slug")
    rebuild.add_argument("--since", type=_instant, help="only runs started at or after this")
    rebuild.add_argument("--limit", type=int, default=10_000)

    seed = sub.add_parser("seed", help="create a local workspace, project and dev key")
    seed.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    return parser


def _price_line(version: str, pattern: str, entry) -> str:  # type: ignore[no-untyped-def]
    return (
        f"{version}  {entry.provider or '*'}/{pattern}  in={entry.input_per_million} "
        f"out={entry.output_per_million}  from={entry.valid_from.date()}"
    )


async def _workspace(conn: AsyncConnection, slug: str) -> Workspace:
    found = await WorkspaceProvisioning(conn).get_by_slug(slug)
    if found is None:
        raise NotFoundError(f"workspace '{slug}' not found")
    return found


async def _project_id(conn: AsyncConnection, workspace: Workspace, slug: str):  # type: ignore[no-untyped-def]
    project = await ProjectRepository(conn, TenantContext(workspace.id)).get_by_slug(slug)
    if project is None:
        raise NotFoundError(f"project '{slug}' not found in workspace '{workspace.slug}'")
    return project.id


def _read_key_file(path: Path) -> str | None:
    return path.read_text().strip() if path.exists() else None


def _write_key_file(path: Path, token: str) -> None:
    """Owner read/write only; the directory is gitignored. The secret is never echoed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        handle.write(token + "\n")


async def _seed(
    conn: AsyncConnection,
    key_file: Path,
    settings: Settings,
    clock: Clock,
    out: TextIO,
    err: TextIO,
) -> None:
    if settings.environment == "production":
        raise DomainError("seed is for local development and refuses to run in production")
    provisioning = WorkspaceProvisioning(conn)
    workspace = await provisioning.get_by_slug(SEED_WORKSPACE[1]) or await provisioning.create(
        name=SEED_WORKSPACE[0], slug=SEED_WORKSPACE[1]
    )
    tenant = TenantContext(workspace.id)
    projects = ProjectRepository(conn, tenant)
    project = await projects.get_by_slug(SEED_PROJECT[1]) or await projects.create(
        name=SEED_PROJECT[0], slug=SEED_PROJECT[1]
    )

    existing = _read_key_file(key_file)
    if existing:
        try:
            await authenticate(conn, existing, clock)
            print(f"dev key in {key_file} is still valid; nothing to do", file=err)
            return
        except AppError:
            pass  # missing, revoked or expired: issue a fresh one below

    keys = ApiKeyRepository(conn, tenant)
    now = clock()
    for old in await keys.list():
        if old.name == SEED_KEY_NAME and old.revoked_at is None:
            await keys.revoke(old.key_id, now)
    created = await keys.create(
        scopes=frozenset({scope_names.EVENTS_WRITE, scope_names.RUNS_READ}),
        project_id=project.id,
        name=SEED_KEY_NAME,
    )
    _write_key_file(key_file, created.token)
    print(
        f"seeded workspace '{workspace.slug}', project '{project.slug}'; "
        f"dev key (events:write, runs:read) written to {key_file}",
        file=err,
    )
    print(f"use it with: Authorization: Bearer $(cat {key_file})", file=err)


async def run(
    argv: Sequence[str],
    settings: Settings,
    *,
    clock: Clock = system_clock,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    engine = create_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            if args.command == "create-workspace":
                ws = await WorkspaceProvisioning(conn).create(name=args.name, slug=args.slug)
                print(f"created workspace '{ws.slug}' ({ws.id})", file=err)
            elif args.command == "create-project":
                ws = await _workspace(conn, args.workspace)
                project = await ProjectRepository(conn, TenantContext(ws.id)).create(
                    name=args.name, slug=args.slug
                )
                print(f"created project '{project.slug}' ({project.id})", file=err)
            elif args.command == "create-key":
                ws = await _workspace(conn, args.workspace)
                project_id = await _project_id(conn, ws, args.project) if args.project else None
                now = clock()
                expires = (
                    now + timedelta(days=args.expires_in_days) if args.expires_in_days else None
                )
                created = await ApiKeyRepository(conn, TenantContext(ws.id)).create(
                    scopes=frozenset(args.scopes),
                    project_id=project_id,
                    name=args.name,
                    expires_at=expires,
                )
                print(f"created key {created.stored.key_id}; the secret is shown once:", file=err)
                print(created.token, file=out)
            elif args.command == "revoke-key":
                ws = await _workspace(conn, args.workspace)
                if parse_key(args.key_id) is not None:
                    raise DomainError("pass the key id (12 characters), not the secret token")
                revoked = await ApiKeyRepository(conn, TenantContext(ws.id)).revoke(
                    args.key_id, clock()
                )
                if not revoked:
                    raise NotFoundError(f"no active key '{args.key_id}' in workspace '{ws.slug}'")
                print(f"revoked key {args.key_id}", file=err)
            elif args.command == "jobs-list":
                for job in await JobQueue(conn).list_jobs(args.status, args.limit):
                    error = (job["last_error"] or "").replace("\n", " ")[:120]
                    print(
                        f"{job['id']}  {job['job_type']}  attempts={job['attempt_count']}  {error}",
                        file=out,
                    )
            elif args.command == "jobs-retry":
                revived = await JobQueue(conn).requeue_dead_letters(None, args.id)
                print(f"requeued {revived} job(s)", file=err)
            elif args.command == "set-pricing-override":
                ws = await _workspace(conn, args.workspace)
                project_id = await _project_id(conn, ws, args.project) if args.project else None
                created_id = await CostRepository(conn, TenantContext(ws.id)).add_override(
                    project_id=project_id,
                    provider=args.provider,
                    model_pattern=args.model_pattern,
                    input_per_million=args.input_per_million,
                    output_per_million=args.output_per_million,
                    cached_input_per_million=args.cached_input_per_million,
                    request_price=args.request_price,
                    valid_from=args.valid_from,
                    note=args.note,
                )
                print(
                    f"added override:{created_id}; run rebuild-costs to apply it to past runs",
                    file=err,
                )
            elif args.command == "list-pricing":
                ws = await _workspace(conn, args.workspace)
                for entry in BUILTIN_ENTRIES:
                    print(_price_line(entry.pricing_version, entry.model_pattern, entry), file=out)
                for record in await CostRepository(conn, TenantContext(ws.id)).overrides():
                    print(
                        _price_line(f"override:{record.id}", record.model_pattern, record.entry()),
                        file=out,
                    )
            elif args.command == "rebuild-costs":
                ws = await _workspace(conn, args.workspace)
                tenant = TenantContext(ws.id)
                project_id = await _project_id(conn, ws, args.project) if args.project else None
                run_ids = await RunRepository(conn, tenant).run_ids(
                    project_id=project_id, since=args.since, limit=args.limit
                )
                queued = await OutboxRepository(conn, tenant).enqueue_summarize(run_ids)
                print(f"queued {queued} of {len(run_ids)} run(s) for re-derivation", file=err)
            elif args.command == "seed":
                await _seed(conn, args.key_file, settings, clock, out, err)
    except (DomainError, ValueError) as exc:
        print(f"error: {exc}", file=err)
        return 2 if isinstance(exc, (AlreadyExistsError, ValueError)) else 1
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(sys.argv[1:], get_settings())))


if __name__ == "__main__":
    main()
