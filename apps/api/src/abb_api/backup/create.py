"""`create`: one consistent bundle (ADR-162).

Consistency, in order:

1. A REPEATABLE READ, READ ONLY transaction exports a snapshot (`pg_export_snapshot()`); the row
   counts, the schema revision and the list of artifact rows are read in that transaction, and
   `pg_dump --snapshot=` dumps exactly that state. The manifest therefore describes the dump, not
   the database a moment later, whatever the application writes meanwhile.
2. The artifact files are copied AFTER the dump. The API writes an artifact's file before its row
   (`artifacts/service.py`), so every row visible to the snapshot has its file by the time the copy
   starts; files that arrive after the dump are extra and harmless (no foreign key points at a
   file). The one way a row can lose its file is a deletion between dump and copy (the retention
   job removes files first, then rows): the copy then fails with exit code 7 and nothing is left
   behind. The rule for operators: do not back up while retention is enforcing (OPERATIONS.md).
3. Before the bundle is finished, every artifact row of the snapshot must have its file in the
   tar. A bundle that fails any step is removed (only the files this run wrote).
"""

import asyncio
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO, Final

from sqlalchemy import text

from abb_api.artifacts.store import valid_key
from abb_api.backup import crypto
from abb_api.backup.errors import (
    EXIT_INCONSISTENT,
    EXIT_REFUSED,
    EXIT_TOOL,
    BackupError,
)
from abb_api.backup.manifest import (
    ARTIFACTS_NAME,
    CHUNK,
    DUMP_NAME,
    ENC_SUFFIX,
    FORMAT_VERSION,
    KEY_TABLES,
    manifest_mac,
    sha256_file,
    write_manifest,
)
from abb_api.backup.pgtools import Conn, find_tool, scrub, tool_version

Log = Callable[..., None]
_DUMP_STDERR: Final = ".pg_dump.stderr"


class Sink:
    """A writable that records the size and SHA-256 of what it is given and passes it on to a
    plain file (0600) or to openssl."""

    def __init__(self, path: Path, passphrase: bytes | None, openssl: str | None = None) -> None:
        self.path = path
        self.sha = hashlib.sha256()
        self.size = 0
        self._writer: BinaryIO | crypto.EncryptingWriter
        if passphrase is None:
            self._writer = os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            )
        else:
            assert openssl is not None
            self._writer = crypto.EncryptingWriter(openssl, passphrase, path, crypto.ITERATIONS)

    def write(self, data: bytes) -> int:
        self.sha.update(data)
        self.size += len(data)
        self._writer.write(data)
        return len(data)

    def close(self) -> None:
        self._writer.close()

    def abort(self) -> None:
        if isinstance(self._writer, crypto.EncryptingWriter):
            self._writer.abort()
        else:
            self._writer.close()


def _entry(sink: Sink, name: str, encrypted: bool) -> dict[str, Any]:
    stored_sha, stored_size = sha256_file(sink.path)
    entry: dict[str, Any] = {"file": name, "size": stored_size, "sha256": stored_sha}
    if encrypted:
        entry["plaintext_size"] = sink.size
        entry["plaintext_sha256"] = sink.sha.hexdigest()
    return entry


def _dump(pg_dump: str, conn: Conn, snapshot: str, sink: Sink, workdir: Path) -> None:
    argv = [
        pg_dump, *conn.args(), "--dbname", conn.database, "--format=custom",
        f"--snapshot={snapshot}", "--no-password",
    ]  # fmt: skip
    stderr_path = workdir / _DUMP_STDERR
    with stderr_path.open("wb") as err:
        proc = subprocess.Popen(  # noqa: S603
            argv, env=conn.env(), stdout=subprocess.PIPE, stderr=err
        )
        assert proc.stdout is not None
        try:
            while chunk := proc.stdout.read(CHUNK):
                if sink.size == 0 and not chunk.startswith(b"PGDMP"):
                    raise BackupError(EXIT_TOOL, "pg_dump did not produce a custom-format archive")
                sink.write(chunk)
        except BaseException:
            proc.kill()
            raise
        finally:
            proc.stdout.close()
            code = proc.wait()
    message = stderr_path.read_text(errors="replace").strip()
    stderr_path.unlink(missing_ok=True)
    if code != 0:
        tail = message.splitlines()[-1] if message else "no output"
        raise BackupError(EXIT_TOOL, f"pg_dump failed: {scrub(tail, conn.secrets())[:300]}")


def _walk_artifacts(root: Path) -> list[tuple[str, Path]]:
    """(key, path) of every artifact file under `root`, sorted. Anything that is not exactly a
    store key (temporary files, strays) is not part of the backup."""
    found: list[tuple[str, Path]] = []
    if not root.is_dir():
        return found
    for ws in sorted(os.scandir(root), key=lambda e: e.name):
        if not ws.is_dir(follow_symlinks=False):
            continue
        for item in sorted(os.scandir(ws.path), key=lambda e: e.name):
            key = f"{ws.name}/{item.name}"
            if item.is_file(follow_symlinks=False) and valid_key(key):
                found.append((key, Path(item.path)))
    return found


class _HashingReader(io.RawIOBase):
    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.sha = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._handle.read(len(buffer))
        buffer[: len(data)] = data
        self.sha.update(data)
        return len(data)


def _tar_artifacts(root: Path, sink: Sink) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as tar:  # type: ignore[call-overload]
        for key, path in _walk_artifacts(root):
            try:
                with path.open("rb") as handle:
                    size = os.fstat(handle.fileno()).st_size
                    info = tarfile.TarInfo(key)
                    info.size, info.mode, info.mtime = size, 0o600, 0
                    reader = _HashingReader(handle)
                    tar.addfile(info, reader)
            except FileNotFoundError:
                continue  # removed since the listing; the row check below decides if it matters
            except OSError:
                raise BackupError(EXIT_TOOL, "an artifact file could not be read") from None
            files.append({"key": key, "size": size, "sha256": reader.sha.hexdigest()})
    return files


async def _snapshot_facts(conn_: Any) -> dict[str, Any]:
    await conn_.execute(text("SET TRANSACTION READ ONLY"))
    snapshot = (await conn_.execute(text("SELECT pg_export_snapshot()"))).scalar_one()
    server_version = (await conn_.execute(text("SHOW server_version"))).scalar_one()
    revision = (await conn_.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    counts: dict[str, int] = {}
    for table in KEY_TABLES:
        counts[table] = (
            await conn_.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608  (constants)
        ).scalar_one()
    return {
        "snapshot": snapshot,
        "server_version": str(server_version),
        "revision": str(revision),
        "tables": counts,
    }


async def _missing_artifact_rows(conn_: Any, present: set[str]) -> int:
    """How many artifact rows of the snapshot have no file in the bundle (streamed)."""
    missing = 0
    result = await conn_.stream(text("SELECT storage_uri FROM artifacts"))
    async for (key,) in result:
        if key not in present:
            missing += 1
    return missing


async def create_bundle(
    out_dir: Path,
    conn: Conn,
    *,
    artifact_dir: Path,
    passphrase: bytes | None,
    environ: Mapping[str, str],
    log: Log,
) -> dict[str, Any]:
    started = time.monotonic()
    pg_dump = find_tool("pg_dump", environ)
    dump_major, dump_banner = tool_version(pg_dump)
    openssl = crypto.openssl_path(environ) if passphrase is not None else None
    encrypted = passphrase is not None
    suffix = ENC_SUFFIX if encrypted else ""

    engine = conn.engine()
    sinks: list[Sink] = []
    try:
        async with engine.connect() as db:
            db = await db.execution_options(isolation_level="REPEATABLE READ")
            async with db.begin():
                facts = await _snapshot_facts(db)
                server_major = int(str(facts["server_version"]).split(".")[0].split()[0])
                if dump_major < server_major:
                    raise BackupError(
                        EXIT_REFUSED,
                        f"pg_dump {dump_major} is older than the server ({server_major}): "
                        "install a matching postgresql-client (ABB_PG_DUMP)",
                    )
                log("backup.snapshot", revision=facts["revision"], tables=len(facts["tables"]))

                dump_sink = Sink(out_dir / (DUMP_NAME + suffix), passphrase, openssl)
                sinks.append(dump_sink)
                await asyncio.to_thread(_dump, pg_dump, conn, facts["snapshot"], dump_sink, out_dir)
                dump_sink.close()
                log("backup.dump", bytes=dump_sink.size)

                # The artifact copy starts only after the dump is complete (the ordering rule).
                art_sink = Sink(out_dir / (ARTIFACTS_NAME + suffix), passphrase, openssl)
                sinks.append(art_sink)
                files = await asyncio.to_thread(_tar_artifacts, artifact_dir, art_sink)
                art_sink.close()
                missing = await _missing_artifact_rows(db, {f["key"] for f in files})
                if missing:
                    raise BackupError(
                        EXIT_INCONSISTENT,
                        f"{missing} artifact row(s) in the snapshot have no file in the artifact "
                        "directory (wrong --artifact-dir, or retention/deletion ran during the "
                        "backup); no bundle was written",
                    )
    except BaseException:
        for sink in sinks:
            try:
                sink.abort()
            except Exception:  # noqa: S110  (best effort; the caller removes the directory)
                pass
        raise
    finally:
        await engine.dispose()

    artifacts = _entry(art_sink, ARTIFACTS_NAME + suffix, encrypted)
    artifacts["count"] = len(files)
    artifacts["total_bytes"] = sum(f["size"] for f in files)
    artifacts["files"] = files
    database = _entry(dump_sink, DUMP_NAME + suffix, encrypted)

    try:
        version = metadata.version("abb-api")
    except metadata.PackageNotFoundError:
        version = "unknown"
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool": {"name": "abb_api.backup", "version": version, "pg_dump": dump_banner},
        "postgres": {"server_version": facts["server_version"]},
        "schema_revision": facts["revision"],
        "tables": facts["tables"],
        "database": database,
        "artifacts": artifacts,
        "total_bytes": database["size"] + artifacts["size"],
        "encryption": None,
    }
    if encrypted:
        assert passphrase is not None
        salt = crypto.new_mac_salt()
        manifest["encryption"] = {
            "scheme": crypto.SCHEME,
            "iterations": crypto.ITERATIONS,
            "mac_salt": salt.hex(),
            "mac_iterations": crypto.ITERATIONS,
        }
        key = crypto.mac_key(passphrase, salt, crypto.ITERATIONS)
        manifest["manifest_mac"] = manifest_mac(key, manifest)
    write_manifest(out_dir, manifest)
    log("backup.done", seconds=round(time.monotonic() - started, 2), bytes=manifest["total_bytes"])
    return manifest


def prepare_output(out_dir: Path) -> Path:
    """The (new or empty) bundle directory, mode 0700. Never writes into a non-empty directory."""
    out_dir = out_dir.expanduser()
    if out_dir.exists():
        if not out_dir.is_dir() or any(out_dir.iterdir()):
            raise BackupError(EXIT_REFUSED, "the output path exists and is not an empty directory")
        out_dir.chmod(0o700)
        return out_dir
    if not out_dir.parent.is_dir():
        raise BackupError(EXIT_REFUSED, "the parent of the output directory does not exist")
    out_dir.mkdir(mode=0o700)
    out_dir.chmod(0o700)  # mkdir's mode is masked by the umask; a bundle is never wider than 0700
    return out_dir


def discard_partial(out_dir: Path) -> None:
    """Remove what a failed run wrote (the directory held nothing else: it was new or empty)."""
    for item in out_dir.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink(missing_ok=True)
        else:
            shutil.rmtree(item, ignore_errors=True)
