"""The `ArtifactStore` contract (INV-7) and its local-filesystem implementation (ADR-030).

A key is `<workspace hex>/<artifact hex>-<sha256 hex>`, built by the service from validated ids and
the content hash, never from client text. Because the hash is part of the key, two uploads can never
overwrite each other's bytes: a racing conflicting upload leaves an orphan file, not corruption.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}-[0-9a-f]{64}$")


class ArtifactStoreError(Exception):
    """The store could not complete an operation (missing file, bad key, I/O failure)."""


def valid_key(key: str) -> bool:
    return _KEY.fullmatch(key) is not None


class ArtifactStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str, offset: int, length: int) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class LocalFsArtifactStore:
    """Files under one root directory, written atomically; blocking I/O runs in a worker thread."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not valid_key(key):
            raise ArtifactStoreError("invalid artifact key")
        path = (self._root / key).resolve()
        if self._root not in path.parents:  # defence in depth: the regex already forbids '..'
            raise ArtifactStoreError("invalid artifact key")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)  # atomic: a reader sees the whole file or none of it
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise ArtifactStoreError("could not write the artifact") from exc

    async def read(self, key: str, offset: int, length: int) -> bytes:
        path = self._path(key)
        return await asyncio.to_thread(self._read, path, offset, length)

    @staticmethod
    def _read(path: Path, offset: int, length: int) -> bytes:
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                return handle.read(length)
        except OSError as exc:
            raise ArtifactStoreError("could not read the artifact") from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
