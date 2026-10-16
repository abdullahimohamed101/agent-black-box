"""Optional passphrase encryption of a bundle with the `openssl` command (ADR-162).

Why openssl and not a library: no new dependency, a format that stock tooling can open
(`openssl enc -d -aes-256-cbc -pbkdf2 -md sha256 -iter N`), and a stream (a dump is never held in
memory or written to disk unencrypted). `enc` alone is not authenticated, so the manifest carries
an HMAC-SHA256 (`manifest_mac`) over every checksum of the ciphertext, keyed by a second PBKDF2
derivation of the passphrase with its own random salt. A wrong passphrase or any modification is
therefore one fixed error, found before a byte is decrypted.

The passphrase is never on a command line or in a log: it comes from a file (mode 0600 at most),
an environment variable NAME, or an inherited file descriptor, and reaches openssl through a pipe.
"""

import hashlib
import os
import secrets
import shutil
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Final

from abb_api.backup.errors import EXIT_INTEGRITY, EXIT_REFUSED, EXIT_TOOL, EXIT_USAGE, BackupError

CIPHER: Final = "aes-256-cbc"
SCHEME: Final = "openssl-enc-aes-256-cbc-pbkdf2-sha256+hmac-sha256"
ITERATIONS: Final = 600_000
# A manifest is read before it is authenticated, so the key-derivation cost it names is hostile
# input: outside this range the bundle is refused instead of burning CPU for hours.
MIN_ITERATIONS: Final = 100_000
MAX_ITERATIONS: Final = 6_000_000
MIN_PASSPHRASE_BYTES: Final = 12
MAX_PASSPHRASE_BYTES: Final = 1024
INTEGRITY_FAILED_MESSAGE: Final = (
    "integrity check failed: the passphrase is wrong or the bundle was modified"
)


def read_passphrase(
    *,
    file: str | None,
    env_name: str | None,
    fd: int | None,
    environ: Mapping[str, str],
) -> bytes | None:
    """The passphrase from exactly one source, or None when no source was given."""
    given = [x for x in (file, env_name, fd) if x is not None]
    if len(given) > 1:
        raise BackupError(EXIT_USAGE, "give only one of --passphrase-file/-env/-fd")
    raw: bytes
    if file is not None:
        path = Path(file)
        try:
            if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise BackupError(
                    EXIT_REFUSED, "the passphrase file is readable by group or others (use 0600)"
                )
            raw = path.read_bytes()
        except OSError:
            raise BackupError(EXIT_USAGE, "the passphrase file cannot be read") from None
    elif env_name is not None:
        value = environ.get(env_name)
        if value is None:
            raise BackupError(EXIT_USAGE, "the passphrase environment variable is not set")
        raw = value.encode()
    elif fd is not None:
        try:
            with os.fdopen(os.dup(fd), "rb") as handle:
                raw = handle.read(MAX_PASSPHRASE_BYTES + 2)
        except OSError:
            raise BackupError(EXIT_USAGE, "the passphrase file descriptor cannot be read") from None
    else:
        return None
    raw = raw.removesuffix(b"\n").removesuffix(b"\r")
    if not raw or len(raw) > MAX_PASSPHRASE_BYTES or b"\0" in raw:
        raise BackupError(EXIT_USAGE, "the passphrase is empty, too long or contains a NUL byte")
    return raw


def require_strong(passphrase: bytes) -> None:
    if len(passphrase) < MIN_PASSPHRASE_BYTES:
        raise BackupError(
            EXIT_USAGE, f"the passphrase must be at least {MIN_PASSPHRASE_BYTES} bytes long"
        )


def checked_iterations(value: object) -> int:
    """The iteration count a manifest names, or ValueError when it is not a sane number."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("iterations")
    count = int(value)
    if not MIN_ITERATIONS <= count <= MAX_ITERATIONS:
        raise ValueError("iterations")
    return count


def mac_key(passphrase: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", b"abb-backup-mac-v1\0" + passphrase, salt, iterations, 32)


def new_mac_salt() -> bytes:
    return secrets.token_bytes(16)


def openssl_path(environ: Mapping[str, str]) -> str:
    found = environ.get("ABB_OPENSSL") or shutil.which("openssl")
    if not found:
        raise BackupError(EXIT_TOOL, "the openssl command is not installed (needed to encrypt)")
    return found


def _command(openssl: str, *, decrypt: bool, iterations: int, fd: int) -> list[str]:
    cmd = [openssl, "enc"]
    if decrypt:
        cmd.append("-d")
    cmd += [f"-{CIPHER}", "-pbkdf2", "-md", "sha256", "-iter", str(iterations)]
    if not decrypt:
        cmd.append("-salt")
    return [*cmd, "-pass", f"fd:{fd}"]


def _passphrase_pipe(passphrase: bytes) -> int:
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, passphrase)  # at most 1 KiB: fits the pipe buffer, never blocks
    finally:
        os.close(write_end)
    return read_end


class EncryptingWriter:
    """A writable that encrypts everything written to it into `target` (mode 0600)."""

    def __init__(self, openssl: str, passphrase: bytes, target: Path, iterations: int) -> None:
        self._out = os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        pipe = _passphrase_pipe(passphrase)
        try:
            self._proc = subprocess.Popen(  # noqa: S603  (fixed argv, no shell)
                _command(openssl, decrypt=False, iterations=iterations, fd=pipe),
                stdin=subprocess.PIPE,
                stdout=self._out,
                stderr=subprocess.DEVNULL,
                pass_fds=(pipe,),
            )
        finally:
            os.close(pipe)
        assert self._proc.stdin is not None
        self._stdin: IO[bytes] = self._proc.stdin

    def write(self, data: bytes) -> int:
        try:
            return self._stdin.write(data)
        except BrokenPipeError:
            raise BackupError(EXIT_TOOL, "openssl stopped while encrypting") from None

    def close(self) -> None:
        try:
            self._stdin.close()
        except BrokenPipeError:
            pass
        code = self._proc.wait()
        self._out.close()
        if code != 0:
            raise BackupError(EXIT_TOOL, "openssl failed while encrypting")

    def abort(self) -> None:
        self._proc.kill()
        self._proc.wait()
        self._out.close()


def decrypt_file(
    openssl: str, passphrase: bytes, source: Path, target: Path, iterations: int
) -> None:
    """Decrypt `source` into `target` (new file, 0600). The MAC was checked first, so a failure
    here is a tool problem, not a wrong passphrase."""
    pipe = _passphrase_pipe(passphrase)
    try:
        with (
            source.open("rb") as src,
            open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as dst,
        ):
            proc = subprocess.run(  # noqa: S603
                _command(openssl, decrypt=True, iterations=iterations, fd=pipe),
                stdin=src,
                stdout=dst,
                stderr=subprocess.DEVNULL,
                pass_fds=(pipe,),
                check=False,
            )
    finally:
        os.close(pipe)
    if proc.returncode != 0:
        raise BackupError(EXIT_INTEGRITY, INTEGRITY_FAILED_MESSAGE)
