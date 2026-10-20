"""The bundle manifest: format, building, reading and checking (ADR-162).

A bundle is a directory:

    MANIFEST.json            readable JSON, never encrypted, nothing secret in it
    database.dump[.enc]      pg_dump -Fc of the whole database, one snapshot
    artifacts.tar[.enc]      the artifact files, copied after the dump

`manifest_mac` (encrypted bundles only) is an HMAC-SHA256 over the rest of the manifest, so the
checksums it lists cannot be replaced without the passphrase.
"""

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

from abb_api.backup.errors import EXIT_INTEGRITY, EXIT_REFUSED, BackupError

FORMAT_VERSION: Final = 1
MANIFEST_NAME: Final = "MANIFEST.json"
DUMP_NAME: Final = "database.dump"
ARTIFACTS_NAME: Final = "artifacts.tar"
ENC_SUFFIX: Final = ".enc"
CHUNK: Final = 1024 * 1024
MAX_MANIFEST_BYTES: Final = 64 * 1024 * 1024

# Row counts recorded at the snapshot and compared after a restore. Fixed so the manifest is
# comparable between backups; every name is a table (the tool never builds SQL from a bundle).
KEY_TABLES: Final = (
    "workspaces",
    "users",
    "workspace_members",
    "projects",
    "api_keys",
    "runs",
    "spans",
    "events",
    "artifacts",
    "findings",
    "outbox_jobs",
    "audit_log",
    "policy_versions",
    "approvals",
    "server_secrets",
    "alert_destinations",
    "alert_rules",
    "alert_notifications",
    "retention_policies",
    "retention_tombstones",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def manifest_mac(mac_key: bytes, manifest: dict[str, Any]) -> str:
    body = {k: v for k, v in manifest.items() if k != "manifest_mac"}
    return hmac.new(mac_key, b"abb-backup-manifest-v1\0" + canonical(body), "sha256").hexdigest()


def write_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    fd = os.open(directory / MANIFEST_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _bad(message: str) -> BackupError:
    return BackupError(EXIT_INTEGRITY, f"{MANIFEST_NAME}: {message}")


def _file_entry(value: object, expected: str, what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("file") != expected:
        raise _bad(f"{what} entry is missing or names an unexpected file")
    if not isinstance(value.get("size"), int) or not _HEX64.match(str(value.get("sha256", ""))):
        raise _bad(f"{what} entry has no valid size or sha256")
    return value


def read_manifest(directory: Path) -> dict[str, Any]:
    """The parsed, shape-checked manifest of a bundle directory (not yet verified against files)."""
    path = directory / MANIFEST_NAME
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise _bad("is implausibly large")
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise BackupError(EXIT_REFUSED, f"{MANIFEST_NAME} not found: not a backup bundle") from None
    except (OSError, ValueError):
        raise _bad("is not valid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError(
            EXIT_REFUSED, f"{MANIFEST_NAME}: unsupported format_version (this tool reads 1)"
        )
    encrypted = manifest.get("encryption") is not None
    suffix = ENC_SUFFIX if encrypted else ""
    _file_entry(manifest.get("database"), DUMP_NAME + suffix, "database")
    arts = _file_entry(manifest.get("artifacts"), ARTIFACTS_NAME + suffix, "artifacts")
    files = arts.get("files")
    if not isinstance(files, list):
        raise _bad("artifacts.files is missing")
    for item in files:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or not isinstance(item.get("size"), int)
            or not _HEX64.match(str(item.get("sha256", "")))
        ):
            raise _bad("artifacts.files has a malformed entry")
    if not isinstance(manifest.get("schema_revision"), str) or not isinstance(
        manifest.get("tables"), dict
    ):
        raise _bad("schema_revision or tables is missing")
    return manifest


def is_encrypted(manifest: dict[str, Any]) -> bool:
    return manifest.get("encryption") is not None


def stored_files(manifest: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """(label, entry) of the bundle's data files as stored on disk."""
    yield "database", manifest["database"]
    yield "artifacts", manifest["artifacts"]
