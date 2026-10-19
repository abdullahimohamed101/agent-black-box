#!/usr/bin/env bash
# `make demo`: record the scripted example agents (no API key, no network beyond your own API) into a local Agent BlackBox.
#
#   scripts/demo.sh            run the demo against the stack from `make dev` or `make up` (API on :8000)
#   scripts/demo.sh reset      DRY RUN: show what would be removed from the demo project (nothing is deleted)
#   scripts/demo.sh reset --confirm   permanently remove the demo project's data (and its keys), nothing else
#
# The demo has its own project (`abb-demo`, workspace `local`) so it never mixes with, and `reset` never touches, your own
# projects. It runs, in order: the coding agent (baseline), the coding agent's wrong-fix variant (a failed run), the simple
# custom agent (a failed tool call and a retry) and the multi-agent job (five agents, a skewed clock, one failed tool).
# Every one is scripted: same agents, spans, calls, errors, retries and costs each time (ids and timestamps differ).
# It never passes --live. Local development only: it refuses ABB_ENVIRONMENT=production.
#
# Environment (all optional): DEMO_API_URL (default http://localhost:8000), DEMO_WEB_URL (default http://localhost:3000),
# DEMO_KEY_FILE (default .local/demo-key), DEMO_RUNS_FILE (append one
# "<name> <run id>" line per run; used by scripts/demo-e2e.sh). ABB_DATABASE_URL comes from .env unless already set.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
if [ -z "${ABB_DATABASE_URL:-}" ]; then
  [ -f .env ] || { echo "no .env: run 'make setup' first" >&2; exit 2; }
  set -a; . ./.env; set +a
fi
[ "${ABB_ENVIRONMENT:-development}" != production ] || { echo "demo is for local development and refuses ABB_ENVIRONMENT=production" >&2; exit 2; }

# These two are the only names the demo ever touches.
WORKSPACE=local
PROJECT=abb-demo
API="${DEMO_API_URL:-http://localhost:8000}"
WEB="${DEMO_WEB_URL:-http://localhost:3000}"
KEY_FILE="${DEMO_KEY_FILE:-$root/.local/demo-key}"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "DEMO FAILED: $*" >&2; exit 1; }

if [ "${1:-}" = reset ]; then
  shift
  confirm=()
  case "${1:-}" in
    "") ;;
    --confirm) confirm=(--confirm "$PROJECT") ;;
    *) echo "usage: scripts/demo.sh reset [--confirm]" >&2; exit 2 ;;
  esac
  [ "${#confirm[@]}" -gt 0 ] && step "PERMANENTLY removing project $PROJECT in workspace $WORKSPACE" || step "dry run: what removing project $PROJECT would delete"
  (cd apps/api && uv run python -m abb_api.cli retention purge-project --workspace "$WORKSPACE" --project "$PROJECT" ${confirm[@]+"${confirm[@]}"})
  if [ "${#confirm[@]}" -gt 0 ]; then rm -f "$KEY_FILE"; else echo "(dry run: nothing was deleted; add --confirm to delete)"; fi
  exit 0
fi

curl -sf --max-time 5 "$API/readyz" >/dev/null || fail "no API at $API (run 'make dev' or 'make up' first)"
cli() { (cd apps/api && uv run python -m abb_api.cli "$@" 2>/dev/null); }

step "provision: workspace '$WORKSPACE' and its dev owner (idempotent, no dev key touched), project '$PROJECT', a demo write key"
mkdir -p "$(dirname "$KEY_FILE")"; chmod 700 "$(dirname "$KEY_FILE")" 2>/dev/null || true
# Not `make seed`: seed re-issues (and so revokes) the dev key whenever its key file is missing, which would break a running dev stack.
cli create-workspace --name "Local development" --slug "$WORKSPACE" >/dev/null || true  # exists after `make seed` or a repeat run
cli add-member --workspace "$WORKSPACE" --email owner@local.test --role OWNER >/dev/null || true  # "already a member" on a repeat run
cli create-project --workspace "$WORKSPACE" --name "Agent BlackBox demo" --slug "$PROJECT" --capture-mode full >/dev/null || true  # exists on a repeat run
valid=0
if [ -s "$KEY_FILE" ] && curl -sf --max-time 5 "$API/v1/runs?limit=1" -H "Authorization: Bearer $(cat "$KEY_FILE")" >/dev/null 2>&1; then valid=1; fi
if [ "$valid" = 0 ]; then
  ( umask 077; cli create-key --workspace "$WORKSPACE" --project "$PROJECT" --scopes events:write artifacts:write runs:read --name demo >"$KEY_FILE.tmp" ) \
    || { rm -f "$KEY_FILE.tmp"; fail "could not create the demo key (is the database up and migrated? 'make setup')"; }
  mv "$KEY_FILE.tmp" "$KEY_FILE"
fi
[[ "$(cat "$KEY_FILE")" == abb_live_* ]] || fail "the demo key file is not a key; delete $KEY_FILE and rerun"

export BLACKBOX_API_KEY="$(cat "$KEY_FILE")" BLACKBOX_ENDPOINT="$API"
# The scripted examples read nothing else: model keys are removed so nothing can call a real provider.
unset ANTHROPIC_API_KEY OPENAI_API_KEY
py() { uv run --project packages/sdk-python python "$@"; }
runs_json=()
record() {  # record <name> <command...>: run an example, keep its last output line (JSON with the run id)
  local name="$1" out rc=0; shift
  step "$name"
  out="$("$@" | tail -1)" || rc=$?
  RUN_ID="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["run_id"])')" || fail "$name printed no run id"
  [ -z "${DEMO_RUNS_FILE:-}" ] || echo "$name $RUN_ID" >>"$DEMO_RUNS_FILE"
  runs_json+=("$name|$RUN_ID|$rc")
  echo "run $RUN_ID (exit $rc)"
}
record coding-baseline    py examples/coding-agent/run_demo.py
record coding-wrong-fix   py examples/coding-agent/run_demo.py --variant wrong-fix   # ends in error on purpose (exit 1)
record custom-support     py examples/python/scripted_agent.py
record multi-agent        py examples/multi-agent/agent.py --skew-ms 1500 --fail-tool

# A scripted run exits 0 except the wrong-fix variant, which is meant to fail.
for entry in "${runs_json[@]}"; do
  IFS='|' read -r name id rc <<<"$entry"
  if [ "$name" = coding-wrong-fix ]; then [ "$rc" = 1 ] || fail "$name should end in error (exit 1), got $rc"
  else [ "$rc" = 0 ] || fail "$name exited $rc"; fi
done

step "done"
echo "Sign in at $WEB (development sign-in: owner@local.test, workspace 'local'), project 'abb-demo'."
for entry in "${runs_json[@]}"; do
  IFS='|' read -r name id rc <<<"$entry"
  printf '  %-16s %s/runs/%s\n' "$name" "$WEB/w/$WORKSPACE/projects/$PROJECT" "$id"
done
echo "Remove the demo data again with: make demo-reset"
