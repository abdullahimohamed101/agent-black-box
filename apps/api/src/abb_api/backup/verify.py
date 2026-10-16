"""`verify`: check a bundle against its manifest without touching any database (ADR-162)."""

import hmac
from pathlib import Path
from typing import Any

from abb_api.backup import crypto
from abb_api.backup.errors import EXIT_INTEGRITY, EXIT_USAGE, BackupError
from abb_api.backup.manifest import (
    is_encrypted,
    manifest_mac,
    read_manifest,
    sha256_file,
    stored_files,
)


def verify_bundle(bundle: Path, passphrase: bytes | None) -> dict[str, Any]:
    """The manifest, after every stored file matched its size and SHA-256 and (encrypted bundles)
    the manifest's HMAC matched the passphrase. Raises BackupError naming the first bad file."""
    manifest = read_manifest(bundle)
    if is_encrypted(manifest):
        if passphrase is None:
            raise BackupError(EXIT_USAGE, "the bundle is encrypted: give the passphrase")
        enc = manifest["encryption"]
        try:
            key = crypto.mac_key(
                passphrase,
                bytes.fromhex(enc["mac_salt"]),
                crypto.checked_iterations(enc["mac_iterations"]),
            )
            ok = hmac.compare_digest(
                manifest_mac(key, manifest), str(manifest.get("manifest_mac", ""))
            )
        except (KeyError, ValueError, TypeError):
            ok = False
        if not ok:
            raise BackupError(EXIT_INTEGRITY, crypto.INTEGRITY_FAILED_MESSAGE)
    for _label, entry in stored_files(manifest):
        path = bundle / entry["file"]
        try:
            digest, size = sha256_file(path)
        except FileNotFoundError:
            raise BackupError(EXIT_INTEGRITY, f"{entry['file']}: file is missing") from None
        except OSError:
            raise BackupError(EXIT_INTEGRITY, f"{entry['file']}: file cannot be read") from None
        if size != entry["size"] or digest != entry["sha256"]:
            raise BackupError(EXIT_INTEGRITY, f"{entry['file']}: checksum mismatch")
    return manifest
