"""The backup command line: `python -m abb_api.backup create|verify|restore` (ADR-162).

    python -m abb_api.backup create --out /backups/2026-10-16 [--encrypt --passphrase-file F]
    python -m abb_api.backup verify /backups/2026-10-16 [--passphrase-file F]
    python -m abb_api.backup restore /backups/2026-10-16 --artifact-dir /data/artifacts

Configuration is the environment (source `.env` first, as `make migrate` does); a database URL or
a passphrase is never a command-line argument:

    create   ABB_BACKUP_DATABASE_URL, else ABB_MIGRATION_DATABASE_URL, else ABB_DATABASE_URL
             (the owner role is recommended: it can read every table), ABB_ARTIFACT_DIR
    restore  ABB_RESTORE_DATABASE_URL (required, deliberately no fallback: it must name a NEW,
             empty database), ABB_ARTIFACT_DIR or --artifact-dir

Exit codes: 0 ok, 1 unexpected, 2 usage/configuration, 3 refused by a safety rule, 4 integrity
(checksum, wrong passphrase, tampering; the message names the file), 5 an external tool failed,
6 the restored result does not match the manifest, 7 the backup would be inconsistent.
Everything except the summary goes to stderr as one JSON object per line.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

import asyncpg
from sqlalchemy.exc import SQLAlchemyError

from abb_api.backup import crypto
from abb_api.backup.create import create_bundle, discard_partial, prepare_output
from abb_api.backup.errors import (
    EXIT_OK,
    EXIT_TOOL,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    BackupError,
)
from abb_api.backup.manifest import is_encrypted
from abb_api.backup.pgtools import Conn, parse_url, scrub
from abb_api.backup.restore import restore_bundle
from abb_api.backup.verify import verify_bundle
from abb_api.obs.textfile import BACKUP_METRICS, TextfileMetrics, textfile_path


def _add_passphrase_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("passphrase (never on the command line itself)")
    group.add_argument("--passphrase-file", help="file holding the passphrase (mode 0600)")
    group.add_argument("--passphrase-env", metavar="NAME", help="NAME of an environment variable")
    group.add_argument("--passphrase-fd", type=int, help="an inherited file descriptor to read")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abb_api.backup", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="write one consistent backup bundle")
    create.add_argument("--out", required=True, type=Path, help="new or empty directory")
    create.add_argument("--artifact-dir", type=Path, help="default: ABB_ARTIFACT_DIR")
    create.add_argument("--encrypt", action="store_true", help="encrypt with a passphrase")
    create.add_argument(
        "--metrics-textfile",
        metavar="PATH",
        help="write Prometheus metrics (last success time, duration, size) to this file for "
        "node_exporter's textfile collector; default: ABB_METRICS_TEXTFILE",
    )
    _add_passphrase_options(create)
    verify = sub.add_parser("verify", help="check a bundle's checksums; touches no database")
    verify.add_argument("bundle", type=Path)
    _add_passphrase_options(verify)
    restore = sub.add_parser("restore", help="load a bundle into an EMPTY database and directory")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--artifact-dir", type=Path, help="default: ABB_ARTIFACT_DIR")
    restore.add_argument("--workdir", type=Path, help="private scratch space for decryption")
    _add_passphrase_options(restore)
    return parser


def _url_from(environ: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        if environ.get(name):
            return environ[name]
    raise BackupError(EXIT_USAGE, f"set {names[0]} (the database URL)")


def _artifact_dir(args: argparse.Namespace, environ: Mapping[str, str]) -> Path:
    chosen = args.artifact_dir or environ.get("ABB_ARTIFACT_DIR")
    if not chosen:
        raise BackupError(EXIT_USAGE, "give --artifact-dir or set ABB_ARTIFACT_DIR")
    return Path(chosen)


def _passphrase(args: argparse.Namespace, environ: Mapping[str, str]) -> bytes | None:
    return crypto.read_passphrase(
        file=args.passphrase_file,
        env_name=args.passphrase_env,
        fd=args.passphrase_fd,
        environ=environ,
    )


def _summary(out: TextIO, **fields: Any) -> None:
    out.write(" ".join(f"{k}={v}" for k, v in fields.items()) + "\n")


async def _run(
    args: argparse.Namespace, environ: Mapping[str, str], out: TextIO, err: TextIO
) -> int:
    secrets: list[str] = []
    started = time.monotonic()
    path = textfile_path(getattr(args, "metrics_textfile", None), environ)
    textfile = TextfileMetrics(path, BACKUP_METRICS) if args.command == "create" and path else None

    def log(event: str, **fields: Any) -> None:
        err.write(json.dumps({"event": event, **fields}, default=str) + "\n")

    def finished(manifest: Mapping[str, Any] | None) -> None:
        """Record a backup run in the metrics file; never raises and never changes the exit code."""
        if textfile is None:
            return
        cat = textfile.catalogue
        if manifest is None:
            cat.backup_runs_total.inc("failed")
        else:
            cat.backup_runs_total.inc("succeeded")
            cat.backup_last_success_timestamp_seconds.set(time.time())
            cat.backup_last_duration_seconds.set(time.monotonic() - started)
            cat.backup_bundle_bytes.set(float(manifest["total_bytes"]))
        textfile.write()

    try:
        if args.command == "verify":
            manifest = verify_bundle(args.bundle, _passphrase(args, environ))
            _summary(
                out,
                result="ok",
                revision=manifest["schema_revision"],
                created_at=manifest["created_at"],
                encrypted=str(is_encrypted(manifest)).lower(),
                artifact_files=manifest["artifacts"]["count"],
                bytes=manifest["total_bytes"],
            )
            return EXIT_OK
        if args.command == "create":
            passphrase = _passphrase(args, environ)
            if args.encrypt and passphrase is None:
                raise BackupError(EXIT_USAGE, "--encrypt needs a --passphrase-file/-env/-fd")
            if passphrase is not None and not args.encrypt:
                raise BackupError(EXIT_USAGE, "a passphrase was given without --encrypt")
            if passphrase is not None:
                crypto.require_strong(passphrase)
            conn: Conn = parse_url(
                _url_from(
                    environ,
                    ("ABB_BACKUP_DATABASE_URL", "ABB_MIGRATION_DATABASE_URL", "ABB_DATABASE_URL"),
                )
            )
            secrets = conn.secrets() + ([passphrase.decode(errors="replace")] if passphrase else [])
            directory = prepare_output(args.out)
            try:
                manifest = await create_bundle(
                    directory,
                    conn,
                    artifact_dir=_artifact_dir(args, environ),
                    passphrase=passphrase,
                    environ=environ,
                    log=log,
                )
            except BaseException:
                discard_partial(directory)
                raise
            finished(manifest)
            _summary(
                out,
                result="ok",
                bundle=directory,
                revision=manifest["schema_revision"],
                encrypted=str(is_encrypted(manifest)).lower(),
                artifact_files=manifest["artifacts"]["count"],
                bytes=manifest["total_bytes"],
            )
            return EXIT_OK
        conn = parse_url(_url_from(environ, ("ABB_RESTORE_DATABASE_URL",)))
        passphrase = _passphrase(args, environ)
        secrets = conn.secrets()
        result = await restore_bundle(
            args.bundle,
            conn,
            artifact_dir=_artifact_dir(args, environ),
            passphrase=passphrase,
            environ=environ,
            workdir=args.workdir,
            log=log,
        )
        _summary(
            out,
            result="ok",
            revision_in_bundle=result["revision_in_bundle"],
            revision_now=result["revision_now"],
            upgraded=str(result["upgraded"]).lower(),
            artifact_files=result["artifact_files"],
            seconds=result["seconds"],
        )
        return EXIT_OK
    except BackupError as exc:
        finished(None)
        err.write(f"error: {scrub(exc.message, secrets)}\n")
        return exc.code
    except (SQLAlchemyError, OSError, asyncpg.PostgresError) as exc:
        finished(None)
        err.write(
            f"error: the database could not be reached or refused the request "
            f"({type(exc).__name__})\n"
        )
        return EXIT_TOOL
    except Exception as exc:  # the class only: a driver message may quote a host or user
        finished(None)
        err.write(f"error: unexpected failure ({type(exc).__name__})\n")
        return EXIT_UNEXPECTED


async def run(argv: Sequence[str], environ: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    """The whole command, awaitable (tests call this; `main` wraps it in an event loop)."""
    return await _run(build_parser().parse_args(list(argv)), environ, out, err)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(
        run(list(argv) if argv is not None else sys.argv[1:], os.environ, sys.stdout, sys.stderr)
    )
