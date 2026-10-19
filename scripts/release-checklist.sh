#!/usr/bin/env bash
# Release checklist (Phase 20, D13). One subcommand per check; later steps add more.
#   licenses   LICENSE is the canonical Apache-2.0 text and every package declares Apache-2.0
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

# SHA-256 of the canonical Apache-2.0 text published by the ASF (LICENSE-2.0.txt).
APACHE_2_0_SHA256="cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

check_licenses() {
  uv run --no-project --python 3.12 python -I - "$APACHE_2_0_SHA256" <<'PY'
import hashlib, json, subprocess, sys, tomllib
from pathlib import Path

want = sys.argv[1]
failed = False
if not Path("LICENSE").is_file():
    print("FAIL LICENSE is missing")
    sys.exit(1)
got = hashlib.sha256(Path("LICENSE").read_bytes()).hexdigest()
if got != want:
    print(f"FAIL LICENSE hash {got} is not the canonical Apache-2.0 text")
    failed = True
else:
    print("ok   LICENSE matches the canonical Apache-2.0 text")

files = subprocess.run(
    ["git", "ls-files", "pyproject.toml", "package.json", "*/pyproject.toml", "*/package.json"],
    capture_output=True, text=True, check=True,
).stdout.split()
if not files:
    print("FAIL no package manifests found")
    sys.exit(1)
for f in sorted(files):
    if "node_modules/" in f:
        continue
    if f.endswith(".toml"):
        data = tomllib.loads(Path(f).read_text())
        lic = data.get("project", {}).get("license")
        name = data.get("project", {}).get("name", "?")
    else:
        data = json.loads(Path(f).read_text())
        lic, name = data.get("license"), data.get("name", "?")
    ok = lic == "Apache-2.0"
    failed |= not ok
    print(f"{'ok  ' if ok else 'FAIL'} {f} ({name}): {lic!r}")
sys.exit(1 if failed else 0)
PY
}

case "${1:-}" in
  licenses) check_licenses ;;
  *) echo "usage: $0 licenses" >&2; exit 2 ;;
esac
