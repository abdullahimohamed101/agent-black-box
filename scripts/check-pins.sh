#!/usr/bin/env bash
# Fails when a GitHub Action is not pinned by commit sha or a container image not by digest.
# Same command in CI and locally (`make check-pins`). Rules: scripts/check_pins.py.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$root/scripts/check_pins.py" "$@"
