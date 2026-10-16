#!/usr/bin/env bash
# Audit every Python lockfile for known vulnerabilities (KI-060). One command for CI and local use:
#   scripts/audit-lockfiles.sh [dir ...]      (default: every uv.lock in the repo)
# pip-audit runs through uvx (a pinned tool, never a project dependency) against requirements exported
# from the frozen lock, with hashes, without resolving anything new. Policy lives in scripts/audit_policy.py
# and scripts/audit-allowlist.txt. A new advisory can fail an unchanged tree: that is the point.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

PIP_AUDIT_VERSION="2.10.1"   # bump deliberately (Dependabot cannot see uvx pins)
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ "$#" -gt 0 ]; then dirs=("$@"); else
  dirs=(); while IFS= read -r lock; do dirs+=("$(dirname "$lock")"); done < <(git ls-files '*uv.lock' | sort)
fi
[ "${#dirs[@]}" -gt 0 ] || { echo "no uv.lock files found" >&2; exit 2; }

status=0
reports=()
for dir in "${dirs[@]}"; do
  name="$(printf '%s' "$dir" | tr '/' '_')"
  # Local path dependencies (the sibling packages in this repo) are ours, not on an index: skip them.
  (cd "$dir" && uv export --frozen --no-emit-project --no-emit-local --all-extras -o "$work/$name.txt" >/dev/null)
  # pip-audit exits 1 when it finds anything; the JSON is judged by audit_policy.py instead.
  uvx "pip-audit==$PIP_AUDIT_VERSION" -r "$work/$name.txt" --require-hashes --disable-pip \
    --progress-spinner off --timeout 30 -f json -o "$work/$name.json" 2>"$work/$name.err" || true
  if [ ! -s "$work/$name.json" ]; then
    echo "audit FAILED to run for $dir:" >&2; tail -5 "$work/$name.err" >&2; status=1; continue
  fi
  reports+=("$dir=$work/$name.json")
done

python3 "$root/scripts/audit_policy.py" "$root/scripts/audit-allowlist.txt" "${reports[@]}" || status=1
exit "$status"
