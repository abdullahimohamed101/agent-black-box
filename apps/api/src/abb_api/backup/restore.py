"""`restore`: load a bundle into an EMPTY database and an EMPTY artifact directory (ADR-162).

The tool never drops, truncates or overwrites anything. A target that already holds a table or a
file is refused (exit 3); there is no flag to override that. The database load is one transaction
(`pg_restore --single-transaction --exit-on-error`), so a failure leaves the database empty. The
rest of the order is: verify the bundle, decrypt into a private work directory, scan the artifact
tar, load the database, unpack the artifacts, compare counts and files with the manifest, and only
then run `alembic upgrade head` (which also applies the role passwords from the environment).
Verification happens BEFORE the upgrade, so counts are compared with the dump's own state.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from abb_api.artifacts.store import valid_key
from abb_api.backup import crypto
from abb_api.backup.errors import (
    EXIT_INTEGRITY,
    EXIT_REFUSED,
    EXIT_TOOL,
    EXIT_VERIFY,
    BackupError,
)
from abb_api.backup.manifest import ARTIFACTS_NAME, CHUNK, DUMP_NAME, is_encrypted
from abb_api.backup.pgtools import Conn, find_tool, run_tool, scrub, tool_version
from abb_api.backup.verify import verify_bundle

Log = Callable[..., None]
ROLES: Final = (
    "abb_runtime",
    "abb_retention",
)  # created by the migrations; present in the dump's ACLs


def alembic_root(environ: Mapping[str, str]) -> Path:
    """The directory holding alembic.ini and migrations/ (the API's root)."""
    candidates = [Path(environ["ABB_ALEMBIC_DIR"])] if environ.get("ABB_ALEMBIC_DIR") else []
    candidates += [Path.cwd(), Path(__file__).resolve().parents[3]]
    for candidate in candidates:
        if (candidate / "alembic.ini").is_file() and (candidate / "migrations").is_dir():
            return candidate
    raise BackupError(
        EXIT_TOOL, "alembic.ini was not found (run from apps/api or set ABB_ALEMBIC_DIR)"
    )


def _script(root: Path) -> ScriptDirectory:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config)


def code_head(root: Path) -> str:
    head = _script(root).get_current_head()
    assert head is not None
    return head


def revision_known(root: Path, revision: str) -> bool:
    try:
        return _script(root).get_revision(revision) is not None
    except Exception:
        return False


def upgrade_to_head(conn: Conn, root: Path, environ: Mapping[str, str]) -> None:
    env = {k: v for k, v in environ.items() if not k.startswith("PG")}
    env["ABB_MIGRATION_DATABASE_URL"] = conn.url  # never on the command line
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        lines = [x for x in proc.stderr.strip().splitlines() if x.strip()]
        tail = scrub(lines[-1] if lines else "no output", conn.secrets())
        raise BackupError(EXIT_TOOL, f"alembic upgrade head failed: {tail[:300]}")


def _prepare_artifact_dir(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise BackupError(EXIT_REFUSED, "the artifact directory exists and is not empty")
        return
    if not path.parent.is_dir():
        raise BackupError(EXIT_REFUSED, "the parent of the artifact directory does not exist")


def _scan_tar(
    tar_path: Path, expected: dict[str, tuple[int, str]], dest: Path | None, written: list[Path]
) -> None:
    """Read the whole tar, check each member against the manifest, and (when `dest` is given)
    unpack it. A member that is not a regular file listed in the manifest is an error."""
    seen: set[str] = set()
    try:
        with tarfile.open(tar_path, mode="r|") as tar:
            for member in tar:
                want = expected.get(member.name)
                if want is None or not member.isreg() or member.name in seen:
                    raise BackupError(EXIT_INTEGRITY, f"{ARTIFACTS_NAME}: unexpected member")
                handle = tar.extractfile(member)
                assert handle is not None
                sha, size = hashlib.sha256(), 0
                out = None
                if dest is not None:
                    target = dest / member.name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    out = os.fdopen(
                        os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                    )
                    written.append(target)
                try:
                    while chunk := handle.read(CHUNK):
                        sha.update(chunk)
                        size += len(chunk)
                        if out is not None:
                            out.write(chunk)
                finally:
                    if out is not None:
                        out.close()
                if (size, sha.hexdigest()) != want:
                    raise BackupError(
                        EXIT_INTEGRITY, f"{ARTIFACTS_NAME}: member {member.name} does not match"
                    )
                seen.add(member.name)
    except tarfile.TarError:
        raise BackupError(EXIT_INTEGRITY, f"{ARTIFACTS_NAME}: not a readable archive") from None
    if seen != set(expected):
        raise BackupError(EXIT_INTEGRITY, f"{ARTIFACTS_NAME}: members are missing")


async def _preflight_database(conn: Conn, manifest: dict[str, Any], log: Log) -> int:
    """Refuse a database that is not empty or too old; make sure the roles in the dump exist.
    Returns the target server's major version."""
    engine = conn.engine()
    try:
        async with engine.connect() as db:
            tables = (
                await db.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"))
            ).scalar_one()
            if tables:
                raise BackupError(
                    EXIT_REFUSED,
                    "the target database is not empty (it has tables); restore needs a new, empty "
                    "database and never drops anything",
                )
            can_create = (
                await db.execute(text("SELECT has_schema_privilege('public', 'CREATE')"))
            ).scalar_one()
            if not can_create:
                raise BackupError(EXIT_REFUSED, "the restoring role cannot create tables in public")
            server = (await db.execute(text("SHOW server_version_num"))).scalar_one()
            major = int(server) // 10000
            wanted = int(str(manifest["postgres"]["server_version"]).split(".")[0])
            if major < wanted:
                raise BackupError(
                    EXIT_REFUSED,
                    f"the target server (PostgreSQL {major}) is older than the one the bundle "
                    f"came from ({wanted})",
                )
            present = {
                r[0]
                for r in await db.execute(
                    text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:r)"), {"r": list(ROLES)}
                )
            }
        missing = [r for r in ROLES if r not in present]
        if missing:
            async with engine.connect() as admin:
                admin = await admin.execution_options(isolation_level="AUTOCOMMIT")
                for role in missing:
                    try:
                        await admin.execute(text(f"CREATE ROLE {role} NOLOGIN"))
                    except Exception:
                        raise BackupError(
                            EXIT_REFUSED,
                            f"the role {role} does not exist on the target server and this "
                            "role cannot create it (create it NOLOGIN, then retry)",
                        ) from None
            log("restore.roles_created", roles=missing)
        return major
    finally:
        await engine.dispose()


async def _count_and_check(
    conn: Conn, manifest: dict[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    engine = conn.engine()
    problems: list[str] = []
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as db:
            revision = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            if revision != manifest["schema_revision"]:
                problems.append("schema revision")
            for table, want in manifest["tables"].items():
                got = (
                    await db.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
                counts[table] = got
                if got != want:
                    problems.append(f"table {table}")
            missing = 0
            result = await db.stream(text("SELECT storage_uri FROM artifacts"))
            async for (key,) in result:
                if not valid_key(key) or not (artifact_dir / key).is_file():
                    missing += 1
            if missing:
                problems.append(f"{missing} artifact file(s)")
    finally:
        await engine.dispose()
    if problems:
        raise BackupError(
            EXIT_VERIFY,
            "the restore finished but does NOT match the manifest ("
            + ", ".join(problems)
            + "): do not use this database; drop it and restore again",
        )
    return counts


async def restore_bundle(
    bundle: Path,
    conn: Conn,
    *,
    artifact_dir: Path,
    passphrase: bytes | None,
    environ: Mapping[str, str],
    workdir: Path | None,
    log: Log,
) -> dict[str, Any]:
    started = time.monotonic()
    manifest = verify_bundle(bundle, passphrase)
    log("restore.verified", revision=manifest["schema_revision"])
    root = alembic_root(environ)
    revision = str(manifest["schema_revision"])
    if not revision_known(root, revision):
        raise BackupError(
            EXIT_REFUSED,
            f"the bundle's schema revision {revision} is unknown to this version of the "
            "software (the bundle is newer): restore with the version that made it",
        )
    pg_restore = find_tool("pg_restore", environ)
    tool_major, _ = tool_version(pg_restore)
    await _preflight_database(conn, manifest, log)
    _prepare_artifact_dir(artifact_dir)

    expected: dict[str, tuple[int, str]] = {}
    for item in manifest["artifacts"]["files"]:
        if not valid_key(item["key"]):
            raise BackupError(
                EXIT_INTEGRITY, f"{ARTIFACTS_NAME}: the manifest names an invalid key"
            )
        expected[item["key"]] = (item["size"], item["sha256"])

    work = Path(tempfile.mkdtemp(prefix="abb-restore-", dir=workdir))
    work.chmod(0o700)  # noqa: ASYNC240  (a single-purpose CLI; blocking is fine)
    written: list[Path] = []
    loaded = False
    try:
        dump_path, tar_path = (
            bundle / manifest["database"]["file"],
            bundle / manifest["artifacts"]["file"],
        )
        if is_encrypted(manifest):
            assert passphrase is not None
            openssl = crypto.openssl_path(environ)
            try:
                iterations = crypto.checked_iterations(manifest["encryption"]["iterations"])
            except (KeyError, ValueError, TypeError):
                raise BackupError(
                    EXIT_INTEGRITY, "the manifest names an unusable key-derivation cost"
                ) from None
            for label, stored, plain in (
                ("database", dump_path, work / DUMP_NAME),
                ("artifacts", tar_path, work / ARTIFACTS_NAME),
            ):
                crypto.decrypt_file(openssl, passphrase, stored, plain, iterations)
                if _file_sha(plain) != manifest[label]["plaintext_sha256"]:
                    raise BackupError(
                        EXIT_INTEGRITY, f"{stored.name}: decrypted content does not match"
                    )
            dump_path, tar_path = work / DUMP_NAME, work / ARTIFACTS_NAME
        _scan_tar(tar_path, expected, None, [])
        log("restore.artifacts_scanned", files=len(expected))

        run_tool(
            [
                pg_restore, *conn.args(), "--dbname", conn.database, "--no-owner",
                "--single-transaction", "--exit-on-error", "--no-password", str(dump_path),
            ],
            conn,
            what="pg_restore",
        )  # fmt: skip
        loaded = True
        log("restore.database_loaded", pg_restore_major=tool_major)

        artifact_dir.mkdir(mode=0o700, exist_ok=True)  # noqa: ASYNC240
        _scan_tar(tar_path, expected, artifact_dir, written)
        counts = await _count_and_check(conn, manifest, artifact_dir)
        upgraded = revision != code_head(root)
        upgrade_to_head(conn, root, environ)
    except Exception as exc:
        for path in written:
            path.unlink(missing_ok=True)
        if loaded and isinstance(exc, BackupError):
            exc.message += " [the database was loaded: discard it before retrying]"
            exc.args = (exc.message,)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    seconds = round(time.monotonic() - started, 2)
    log("restore.done", seconds=seconds, upgraded=upgraded)
    return {
        "revision_in_bundle": revision,
        "revision_now": code_head(root),
        "upgraded": upgraded,
        "tables": counts,
        "artifact_files": len(expected),
        "seconds": seconds,
    }


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
