"""Find planted secrets anywhere they could have been stored: every table and every artifact file.

A test plants sentinel strings (an AWS-style key, a private key body ...) in what an SDK sends and
then asks whether any of them exists in the database or on disk. Reused by the Phase 13
verification suite. The scan is deliberately dumb: it reads every row as text, so a column added
later is covered without anyone remembering to list it.
"""

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db.tables import metadata

AWS_SENTINEL = "AKIAIOSFODNN7EXAMPLE"  # AWS's documented example key, not a credential
KEY_BODY_SENTINEL = "MIIEpAIBAAKCAQEAplantedsentinelbody0123456789"
PRIVATE_KEY_SENTINEL = (
    f"-----BEGIN RSA PRIVATE KEY-----\n{KEY_BODY_SENTINEL}\n-----END RSA PRIVATE KEY-----"
)
GITHUB_SENTINEL = "ghp_" + "Zz9Yy8Xx7W" * 4


async def find_in_database(engine: AsyncEngine, needles: Iterable[str]) -> dict[str, list[str]]:
    """{table: [needles found in at least one row]} for every table in the schema."""
    wanted = list(needles)
    found: dict[str, list[str]] = {}
    async with engine.connect() as conn:
        for table in metadata.sorted_tables:
            for needle in wanted:
                hit = await conn.execute(
                    text(
                        f'SELECT 1 FROM "{table.name}" AS r '  # noqa: S608 - names from metadata
                        "WHERE position(:needle in r::text) > 0 LIMIT 1"
                    ),
                    {"needle": needle},
                )
                if hit.first() is not None:
                    found.setdefault(table.name, []).append(needle)
    return found


def find_on_disk(root: Path, needles: Iterable[str]) -> dict[str, list[str]]:
    """{relative path: [needles found]} for every file under `root` (raw bytes, any encoding)."""
    wanted = [n.encode() for n in needles]
    found: dict[str, list[str]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        hits = [n.decode() for n in wanted if n in data]
        if hits:
            found[str(path.relative_to(root))] = hits
    return found
