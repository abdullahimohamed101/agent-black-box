#!/usr/bin/env bash
# Runs the command blocks of the user-facing docs that are marked `<!-- check:run -->` and reference-checks the rest
# (make targets, paths, compose profiles, CLI subcommands, ABB_* names). No stack, no Docker, no network beyond what
# `uv run` needs to resolve its environment. Rules: scripts/docs_commands_check.py. Same command in CI and locally.
#   scripts/docs-commands-check.sh            check the four docs
#   scripts/docs-commands-check.sh --no-run   reference checks only (no command is executed)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$root/scripts/docs_commands_check.py" "$@"
