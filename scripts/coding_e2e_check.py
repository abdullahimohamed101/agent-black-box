"""Server-side half of the coding-agent E2E: planted secrets must be absent from stored data.

Reads CODING_E2E_PLANTED (JSON list), CODING_E2E_ARTIFACT_DIR and the database URL (ABB_DATABASE_URL); prints
what it searched and exits 1 if any planted literal is found in the events table (attributes, payload, tags), in
any artifact file on disk, or if the expected redaction markers are missing from the stored artifacts.
Run with: uv run --project apps/api python scripts/coding_e2e_check.py <run_id>
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg


async def main(run_id: str) -> int:
    planted: list[str] = json.loads(os.environ["CODING_E2E_PLANTED"])
    url = os.environ["ABB_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "select event_type, attributes::text as a, coalesce(payload::text, '') as p, tags::text as t "
            "from events"
        )
        artifacts = await conn.fetch(
            "select artifact_type, name, size_bytes, storage_uri, run_id::text as run from artifacts"
        )
    finally:
        await conn.close()
    problems: list[str] = []
    for row in rows:
        blob = row["a"] + row["p"] + row["t"]
        problems += [f"event {row['event_type']} contains a planted secret" for s in planted if s in blob]
    for row in artifacts:  # names and kinds are user text too
        blob = f"{row['artifact_type']} {row['name'] or ''} {row['storage_uri']}"
        problems += ["artifact row (name/kind/uri) contains a planted secret" for s in planted if s in blob]
    root = Path(os.environ["CODING_E2E_ARTIFACT_DIR"])
    files = [p for p in root.rglob("*") if p.is_file()]
    text = ""
    for path in files:
        data = path.read_bytes().decode("utf-8", errors="replace")
        text += data
        problems += [f"artifact file {path.name[:12]} contains a planted secret" for s in planted if s in data]
    for marker in ("[REDACTED:aws_access_key]", "[REDACTED:private_key]", "[REDACTED:env]"):
        if marker not in text:
            problems.append(f"expected marker {marker} not found in stored artifacts")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "events_scanned": len(rows),
                "artifact_rows": len(artifacts),
                "artifact_files": len(files),
                "artifact_bytes": sum(len(p.read_bytes()) for p in files),
                "planted_literals": len(planted),
                "problems": problems,
            }
        )
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
