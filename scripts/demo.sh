#!/usr/bin/env bash
# `make demo`: record the scripted example agents (no API key, no network beyond your own API) into a local Agent BlackBox.
#
#   scripts/demo.sh            run the demo against the stack from `make dev` or `make up` (API on :8000)
#   scripts/demo.sh reset      DRY RUN: show what would be removed from the demo project (nothing is deleted)
#   scripts/demo.sh reset --confirm   permanently remove the demo project's data (and its keys), nothing else
#
# The demo has its own project (`abb-demo`, workspace `local`) so it never mixes with, and `reset` never touches, your own
# projects. It records twelve scripted runs: a coding agent (a good run and a failed one), a support agent (a clean run, a run
# that recovers after a timeout, a flaky one, one refused by its ticket service and one whose ticket service is down, which
# fails), an ops agent (the run the security findings come from), the multi-agent job (five agents, a skewed clock, one failed
# tool) and a coding agent that asks to push to main and WAITS for a person (the pending approval on the Review page).
# Every one is scripted: same agents, spans, calls, errors, retries and costs each time (ids and timestamps differ).
# The waiting agent is a background process; `make demo-reset` (or the next demo) stops it. It needs the API to accept
# development sign-in sessions (ABB_ALLOW_DEV_SESSIONS=1 on the API, never in production) to publish the demo policy; without
# them the demo says so and skips only that run.
# It never passes --live. Local development only: it refuses ABB_ENVIRONMENT=production.
#
# Environment (all optional): DEMO_API_URL (default http://localhost:8000), DEMO_WEB_URL (default http://localhost:3000),
# DEMO_KEY_FILE (default .local/demo-key), DEMO_RUNS_FILE (append one "<name> <run id>" line per run; used by scripts/demo-e2e.sh),
# DEMO_STATE_DIR (default .local/demo: the waiting agent's pid file and log), DEMO_KEEP_WAITERS=1 (do not stop an earlier waiting
# agent first), DEMO_PACE (scale of the real pauses of the multi-agent job, default 1; 0 for none). ABB_DATABASE_URL comes from .env unless already set.
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
STATE_DIR="${DEMO_STATE_DIR:-$root/.local/demo}"; PACE="${DEMO_PACE:-1}"
mkdir -p "$STATE_DIR"
stop_waiters() {  # SIGINT: the SDK withdraws its pending approval before it exits
  [ -f "$STATE_DIR/waiters.pid" ] || return 0
  while read -r pid; do [ -n "$pid" ] && kill -INT "$pid" 2>/dev/null || true; done <"$STATE_DIR/waiters.pid"
  sleep 1; rm -f "$STATE_DIR/waiters.pid"
}
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
  [ "${#confirm[@]}" -gt 0 ] && stop_waiters
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
[ -n "${DEMO_KEEP_WAITERS:-}" ] || stop_waiters
# Realism: the scripted agents record believable work without waiting for it. --started-ago puts a run on its own day and hour
# (the agents' virtual clock: examples/python/demo_clock.py, sent through the SDK like any event), model and tool latencies pass
# instantly (a run lasts 10 s to 90 s on the run page), --tokens-scale gives them real-sized contexts (a run costs 3 cents to
# 60 cents) and --model-name picks one of two made-up models. The week then has a daily-spend curve and runs of different ages.
ago() {  # ago <days back> <hour of day, UTC>: how many hours before now that moment was
  python3 -c 'import sys,time; d,h=map(float,sys.argv[1:]); n=time.time(); print(f"{(n-(n-n%86400-d*86400+h*3600))/3600:.4f}")' "$1" "$2"
}
# Oldest first: the runs list shows the newest on top, so the failed support run is second from the top (the waiter and the
# multi-agent job are recorded in real time, "now").
record coding-baseline    py examples/coding-agent/run_demo.py --started-ago "$(ago 6 14)"
record support-recovers   py examples/python/scripted_agent.py --pace 0 --started-ago "$(ago 5 10.5)" --tokens-scale 16
record support-clean-1    py examples/python/scripted_agent.py --pace 0 --ticket T-1043 --variant clean --started-ago "$(ago 4 15)" --tokens-scale 20
record support-flaky      py examples/python/scripted_agent.py --pace 0 --ticket T-1044 --variant flaky --started-ago "$(ago 4 9)" --tokens-scale 14
record coding-wrong-fix   py examples/coding-agent/run_demo.py --variant wrong-fix --name "Fix session refresh bug" --started-ago "$(ago 3 11)"   # ends in error on purpose (exit 1)
record support-clean-2    py examples/python/scripted_agent.py --pace 0 --ticket T-1045 --variant clean --started-ago "$(ago 2 16)" --tokens-scale 22
record support-denied     py examples/python/scripted_agent.py --pace 0 --ticket T-1046 --variant denied --started-ago "$(ago 2 10)" --tokens-scale 28        # ends in error on purpose (exit 1)
record ops-cleanup        py examples/python/ops_agent.py --pace 0 --started-ago "$(ago 1 13.5)" --tokens-scale 10 --model-name scripted-large
MULTI_PACE="$(awk -v p="$PACE" 'BEGIN { print p * 10 }')"  # its pretend delays are real sleeps (threads and a second process): about half a minute
record multi-agent        py examples/multi-agent/agent.py --skew-ms 1500 --fail-tool --pace "$MULTI_PACE"

# The waiting agent: publish the demo policy as the demo owner, then start an agent that asks to push to main and waits.
waiter_started=0
SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email owner@local.test --hours 1 || true)"
if [ -n "$SESSION" ] && DEMO_API="$API" DEMO_WEB="$WEB" DEMO_SESSION="$SESSION" DEMO_WORKSPACE="$WORKSPACE" DEMO_PROJECT="$PROJECT" \
     uv run --project apps/api python scripts/demo_policy.py publish examples/coding-agent/policies/demo.yaml >"$STATE_DIR/policy.log" 2>&1; then
  step "coding-awaiting-approval (a background agent that waits for a person)"
  wlog="$STATE_DIR/waiter-$(date +%s)-$RANDOM.log"
  BLACKBOX_POLICY=enforce nohup uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --policy-demo \
    --name "Push session hotfix" >"$wlog" 2>&1 </dev/null &
  echo $! >>"$STATE_DIR/waiters.pid"
  for _ in $(seq 1 90); do grep -q '"event": "approval_pending"' "$wlog" 2>/dev/null && break; sleep 0.5; done
  grep -q '"event": "approval_pending"' "$wlog" || fail "the waiting agent did not ask for approval: $(tail -3 "$wlog")"
  approval_id="$(grep -m1 '"event": "approval_pending"' "$wlog" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["approval_id"])')"
  RUN_ID="$(DEMO_API="$API" DEMO_WEB="$WEB" DEMO_SESSION="$SESSION" DEMO_WORKSPACE="$WORKSPACE" DEMO_PROJECT="$PROJECT" \
    uv run --project apps/api python scripts/demo_policy.py run-of "$approval_id")" || fail "could not find the waiting run"
  [ -z "${DEMO_RUNS_FILE:-}" ] || echo "coding-awaiting-approval $RUN_ID" >>"$DEMO_RUNS_FILE"
  runs_json+=("coding-awaiting-approval|$RUN_ID|0")
  echo "run $RUN_ID is waiting for approval (log: ${wlog#$root/})"
else
  echo "skipped the waiting agent: the API does not accept development sessions (set ABB_ALLOW_DEV_SESSIONS=1 on the API to see a pending approval; see $STATE_DIR/policy.log)" >&2
fi
unset SESSION
record support-outage     py examples/python/scripted_agent.py --pace 0 --ticket T-1047 --variant outage --started-ago 3.2 --tokens-scale 18         # ends in error on purpose (exit 1)
record support-clean-3    py examples/python/scripted_agent.py --pace 0 --ticket T-1048 --variant clean --started-ago 1.4 --tokens-scale 24

# A scripted run exits 0 except the ones that are meant to fail.
for entry in "${runs_json[@]}"; do
  IFS='|' read -r name id rc <<<"$entry"
  case "$name" in
    coding-wrong-fix|support-denied|support-outage) [ "$rc" = 1 ] || fail "$name should end in error (exit 1), got $rc" ;;
    *) [ "$rc" = 0 ] || fail "$name exited $rc" ;;
  esac
done

step "done"
echo "Sign in at $WEB (development sign-in: owner@local.test, workspace 'local'), project 'abb-demo'."
for entry in "${runs_json[@]}"; do
  IFS='|' read -r name id rc <<<"$entry"
  printf '  %-16s %s/runs/%s\n' "$name" "$WEB/w/$WORKSPACE/projects/$PROJECT" "$id"
done
echo "Remove the demo data again with: make demo-reset"
