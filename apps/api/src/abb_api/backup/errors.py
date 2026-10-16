"""Fixed exit codes and the one exception the backup tool raises (ADR-162).

A message never contains a passphrase, a connection URL or a secret value: it names the step, the
file, or the check that failed.
"""

from typing import Final

EXIT_OK: Final = 0
EXIT_UNEXPECTED: Final = 1
EXIT_USAGE: Final = 2  # bad flags or missing configuration (also argparse's own code)
EXIT_REFUSED: Final = 3  # a safety rule said no: non-empty target, newer schema, wide permissions
EXIT_INTEGRITY: Final = 4  # checksum, wrong passphrase or tampering: names the file
EXIT_TOOL: Final = 5  # pg_dump, pg_restore, openssl or alembic failed
EXIT_VERIFY: Final = 6  # the restore finished but the result does not match the manifest
EXIT_INCONSISTENT: Final = 7  # the backup would not be consistent (artifact file missing)


class BackupError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
