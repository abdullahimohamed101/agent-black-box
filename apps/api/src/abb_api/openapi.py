"""Export the OpenAPI document to the committed `apps/api/openapi.json` (spec §111, plan D18).

    python -m abb_api.openapi            # write the file
    python -m abb_api.openapi --check    # exit 1 if the committed file is stale

The committed file is the reviewable API contract and the input for the typed web client.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from abb_api.core.config import Settings
from abb_api.main import create_app

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def build_document() -> dict[str, Any]:
    # A placeholder URL: building the document never connects to a database.
    settings = Settings(environment="test", database_url="postgresql+asyncpg://openapi/none")
    document: dict[str, Any] = create_app(settings).openapi()
    return document


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)
    rendered = render(build_document())
    if args.check:
        if not args.path.exists() or args.path.read_text(encoding="utf-8") != rendered:
            print("openapi.json is stale (run `make openapi`)", file=sys.stderr)
            return 1
        return 0
    args.path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
