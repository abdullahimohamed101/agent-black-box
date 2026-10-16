"""`python -m abb_api.rotate alerts [--new-key] [--dry-run]`.

    python -m abb_api.rotate alerts --new-key      add a key, then re-seal every destination
    python -m abb_api.rotate alerts                resume / converge: re-seal what is not yet
                                                   under the newest key (after restarting the
                                                   API and worker, or after an interruption)
    python -m abb_api.rotate alerts --dry-run      read-only report; writes nothing

Connects as the database owner: ABB_ROTATION_DATABASE_URL, else ABB_MIGRATION_DATABASE_URL, else
ABB_DATABASE_URL (the runtime role also works, but ADR-150 names the owner). The URL and every
secret stay out of the output. Exit codes: 0 finished, 1 some destination could not be re-sealed
(listed by id), 2 usage or configuration, 5 database unreachable.
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

import asyncpg
from sqlalchemy.exc import SQLAlchemyError

from abb_api.backup.errors import EXIT_OK, EXIT_TOOL, EXIT_UNEXPECTED, EXIT_USAGE
from abb_api.backup.pgtools import parse_url
from abb_api.rotate.alerts import RotationReport, rotate_alerts

_URLS = ("ABB_ROTATION_DATABASE_URL", "ABB_MIGRATION_DATABASE_URL", "ABB_DATABASE_URL")
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abb_api.rotate", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    alerts = sub.add_parser("alerts", help="rotate the alert-encryption key and re-seal")
    alerts.add_argument("--new-key", action="store_true", help="add a new key row first")
    alerts.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    alerts.add_argument("--batch-size", type=int, default=50, help="destinations per page (1-1000)")
    alerts.add_argument("--max", type=int, default=100_000, dest="max_destinations")
    return parser


def _summary(report: RotationReport, out: TextIO) -> None:
    by_key = ",".join(f"{k}:{n}" for k, n in sorted(report.blobs_by_key.items())) or "-"
    fields: dict[str, Any] = {
        "dry_run": str(report.dry_run).lower(),
        "newest_key_id": report.newest_key_id,
        "key_added": report.key_added or "-",
        "destinations": report.destinations,
        "resealed": report.resealed,
        "would_reseal": report.would_reseal,
        "already_current": report.already_current,
        "failed": len(report.failed),
        "blobs_by_key": by_key,
        "older_blobs": report.older_blobs,
        "truncated": str(report.truncated).lower(),
    }
    out.write(" ".join(f"{k}={v}" for k, v in fields.items()) + "\n")
    if report.failed:
        out.write("failed_destinations=" + ",".join(str(d) for d in report.failed) + "\n")
    if report.dry_run:
        out.write("verdict=dry_run_nothing_changed\n")
    elif report.can_retire_old_keys:
        out.write("verdict=old_keys_can_be_retired\n")
    else:
        out.write(f"verdict=old_keys_still_in_use older_blobs={report.older_blobs}\n")


async def run(argv: Sequence[str], environ: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    args = build_parser().parse_args(list(argv))
    if not 1 <= args.batch_size <= 1000 or args.max_destinations < 1:
        err.write("error: --batch-size must be 1-1000 and --max at least 1\n")
        return EXIT_USAGE
    url = next((environ[n] for n in _URLS if environ.get(n)), None)
    if url is None:
        err.write(f"error: set {_URLS[0]} (the owner role's database URL)\n")
        return EXIT_USAGE

    def progress(event: str, **fields: Any) -> None:
        err.write(json.dumps({"event": event, **fields}) + "\n")

    try:
        conn = parse_url(url)
        report = await rotate_alerts(
            conn.url,
            dry_run=args.dry_run,
            new_key=args.new_key,
            batch_size=args.batch_size,
            max_destinations=args.max_destinations,
            progress=progress,
        )
    except (SQLAlchemyError, OSError, asyncpg.PostgresError) as exc:
        err.write(
            "error: the database could not be reached or refused the request "
            f"({type(exc).__name__})\n"
        )
        return EXIT_TOOL
    except Exception as exc:  # the class only
        err.write(f"error: unexpected failure ({type(exc).__name__})\n")
        return EXIT_UNEXPECTED
    _summary(report, out)
    return EXIT_FAILED if report.failed else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(
        run(list(argv) if argv is not None else sys.argv[1:], os.environ, sys.stdout, sys.stderr)
    )
