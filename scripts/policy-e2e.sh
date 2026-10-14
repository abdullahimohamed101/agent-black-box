#!/usr/bin/env bash
# Policy engine and approvals E2E (Phase 14, gates 1-8): a dedicated database (abb_policy_e2e), the real API on :8200, a SECOND
# API process on :8202 (same database; the scripts stop and restart it to simulate an outage, by the PID they started), a
# worker, the DEVELOPMENT-ONLY fake OIDC provider on :8201 and the built web server on :3200 (started by Playwright).
#   1. a protected demo action blocked or paused until a human decides: the real SDK runs examples/coding-agent --policy-demo
#      against the starter policy; a real browser (OIDC sign-in as a second admin) approves or denies; expiry falls back to deny
#   2. a replayed approval is rejected in every way (different intent, second use, expiry, other workspace/project, policy deny)
#   3. (evaluation determinism is covered by the API suite: golden policies and property tests)
#   4. failure modes against the live stack: outage, timeouts, per-class defaults, cached table, expiry fallbacks
#   5. the state machine under real concurrency, then illegal SQL as the runtime role
#   6. authorization: roles, self-approval, API keys, workspaces, scrubbed arguments, audit
#   7. SDK safety: a SIGINT during a wait cancels the approval; only documented exceptions; bounded waits; stdlib-only
#   8. the dry run compared with an independent evaluation; decision and ingestion latency (docs/benchmarks/phase-14-policy-e2e.md)
# Then Playwright drives a real browser through the OIDC flow. Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`.
# Never touches the shared dev/test databases. POLICY_E2E_STAGES=a,b limits the stages (development aid; CI runs them all).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${POLICY_E2E_DB_NAME:-abb_policy_e2e}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
export ABB_FINDINGS_DELAY_SECONDS=1
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000      # the benchmark measures the pipeline, not the default throttle
export ABB_POLICY_DECIDE_PER_MINUTE_KEY=1000000      # ditto for decisions; a flood test lives in the API suite (test_flood.py)
export ABB_POLICY_DECIDE_PER_MINUTE_WORKSPACE=1000000
export ABB_POLICY_WRITES_PER_MINUTE=1000 ABB_POLICY_VALIDATIONS_PER_MINUTE=100000 ABB_POLICY_DRY_RUNS_PER_MINUTE=1000
export ABB_AUDIT_DENIALS_PER_MINUTE=1000             # the authorization probes would otherwise outrun the audit throttle
PORT="${POLICY_E2E_API_PORT:-8200}"; API="http://localhost:$PORT"
OIDC_PORT="${POLICY_E2E_OIDC_PORT:-8201}"
FAIL_PORT="${POLICY_E2E_FAIL_PORT:-8202}"; FAIL_API="http://localhost:$FAIL_PORT"
SLOW_PORT="${POLICY_E2E_SLOW_PORT:-8203}"
export ABB_WEB_ORIGIN="http://localhost:3200"        # fixed: playwright.config.ts starts the web server there
export ABB_ENVIRONMENT=development                   # http issuers and dev sessions exist only here and in test
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT"
export ABB_OIDC_CLIENT_ID="abb-dev"
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  # The check scripts start (and stop) their own processes in process groups of their own; they clean up after themselves,
  # and anything still recorded in $WORK/spawned.pids is a process group this run created.
  if [ -f "$WORK/spawned.pids" ]; then while read -r g; do [ -n "$g" ] && kill -TERM -- "-$g" 2>/dev/null || true; done <"$WORK/spawned.pids"; fi
  # POLICY_E2E_LOG_DIR keeps the server logs of a failed run (they hold no payload bodies, secrets or key tokens: INV-5).
  if [ -n "${POLICY_E2E_LOG_DIR:-}" ]; then mkdir -p "$POLICY_E2E_LOG_DIR" && cp "$WORK"/*.log "$POLICY_E2E_LOG_DIR"/ 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "POLICY-E2E FAILED: $*" >&2; exit 1; }
stage() { [ -z "${POLICY_E2E_STAGES:-}" ] || [[ ",$POLICY_E2E_STAGES," == *",$1,"* ]]; }
cd apps/api

step "create database $DB_NAME if missing, migrate to head"
ADMIN_URL="${ABB_DATABASE_URL%/*}/postgres" DB_NAME="$DB_NAME" uv run python - <<'PY'
import asyncio, os, asyncpg
async def main():
    url = os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    if not await conn.fetchval("select 1 from pg_database where datname=$1", os.environ["DB_NAME"]):
        await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
uv run alembic upgrade head >/dev/null 2>&1

step "start the fake identity provider (:$OIDC_PORT), the API (:$PORT) and a worker ($FAIL_PORT is started by the checks)"
for u in "$API/readyz" "$FAIL_API/readyz" "http://localhost:$OIDC_PORT/.well-known/openid-configuration"; do
  if curl -sf "$u" >/dev/null 2>&1; then fail "something is already serving $u; stop it or set POLICY_E2E_API_PORT / _FAIL_PORT / _OIDC_PORT"; fi
done
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 60); do
  curl -sf "$API/readyz" >/dev/null && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null && break
  sleep 0.5
done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null || fail "fake provider did not start: $(tail -5 "$WORK/oidc.log")"

step "provision the world (generated, local-only; secrets never printed): two workspaces, people for every role, projects, keys created by the owner"
suffix="$(date +%s)$RANDOM"
WS="policy-e2e-$suffix"; WS2="policy-other-$suffix"
OWNER="owner-$suffix@policy.test"; ADMIN="admin-$suffix@policy.test"; DEV="developer-$suffix@policy.test"
VIEWER="viewer-$suffix@policy.test"; BILLING="billing-$suffix@policy.test"; ADMIN2="admin2-$suffix@other.test"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Policy E2E" --slug "$WS" >/dev/null
cli create-workspace --name "Policy Other" --slug "$WS2" >/dev/null
for p in full expiry failmodes races ui bench other; do cli create-project --workspace "$WS" --name "$p" --slug "$p" --capture-mode full >/dev/null; done
cli create-project --workspace "$WS2" --name Foreign --slug foreign --capture-mode full >/dev/null
for pair in "$OWNER:OWNER" "$ADMIN:ADMIN" "$DEV:DEVELOPER" "$VIEWER:VIEWER" "$BILLING:BILLING"; do
  cli add-member --workspace "$WS" --email "${pair%%:*}" --role "${pair##*:}" >/dev/null
done
cli add-member --workspace "$WS2" --email "$ADMIN2" --role ADMIN >/dev/null
session() { ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$1" --hours 3; }
export E2E_OWNER_SESSION="$(session "$OWNER")" E2E_ADMIN_SESSION="$(session "$ADMIN")" E2E_DEVELOPER_SESSION="$(session "$DEV")"
export E2E_VIEWER_SESSION="$(session "$VIEWER")" E2E_BILLING_SESSION="$(session "$BILLING")" E2E_OTHER_ADMIN_SESSION="$(session "$ADMIN2")"
for s in "$E2E_OWNER_SESSION" "$E2E_ADMIN_SESSION" "$E2E_DEVELOPER_SESSION" "$E2E_VIEWER_SESSION" "$E2E_BILLING_SESSION" "$E2E_OTHER_ADMIN_SESSION"; do
  [ -n "$s" ] || fail "could not mint sessions"
done
# Planted values: unique per run (the database is reused), shaped like the real thing so the scanner would catch them.
rand() { LC_ALL=C tr -dc "$1" </dev/urandom | head -c "$2" || true; }
export E2E_PLANT="PLANTPOL${suffix}"
export E2E_AWS="AKIA$(rand 'A-Z0-9' 16)"; export E2E_PASSWORD="pw-$(rand 'a-z0-9' 14)-e2e"
export E2E_API="$API" E2E_FAIL_API="$FAIL_API" E2E_SLOW_PORT="$SLOW_PORT" E2E_WORKSPACE="$WS" E2E_WORKSPACE2="$WS2"
export E2E_ORIGIN="$ABB_WEB_ORIGIN" E2E_STATE="$WORK/state.json" E2E_WORK="$WORK" E2E_ROOT="$root"
export E2E_OWNER_EMAIL="$OWNER" E2E_ADMIN_EMAIL="$ADMIN" E2E_DEVELOPER_EMAIL="$DEV" E2E_VIEWER_EMAIL="$VIEWER" E2E_BILLING_EMAIL="$BILLING"
export E2E_LOGS="$WORK/api.log:$WORK/api-fail.log:$WORK/worker.log:$WORK/oidc.log:$WORK/demo-approve.log:$WORK/demo-deny.log:$WORK/demo-expiry.log"
export E2E_ARTIFACT_DIR="$ABB_ARTIFACT_DIR" E2E_RUNTIME_ROLE="${DB_NAME}_rt"
# A login role in the application's least-privilege group (abb_runtime): gate 5 connects as it and tries illegal SQL.
export E2E_RUNTIME_PASSWORD="rt-$(rand 'a-z0-9' 24)"
ROLE="$E2E_RUNTIME_ROLE" RT_PW="$E2E_RUNTIME_PASSWORD" ADMIN_URL="$ABB_DATABASE_URL" uv run python - <<'PY'
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    role, pw = os.environ["ROLE"], os.environ["RT_PW"]
    if not await conn.fetchval("select 1 from pg_roles where rolname=$1", role):
        await conn.execute(f'create role "{role}" login password \'{pw}\' in role abb_runtime')
    else:
        await conn.execute(f'alter role "{role}" with login password \'{pw}\'')
    await conn.close()
asyncio.run(main())
PY
: >"$WORK/state.json"; chmod 600 "$WORK/state.json"
check() { uv run python "$root/scripts/policy_e2e_check.py" "$@"; }
sdk_driver() { (cd "$root" && uv run --project packages/sdk-python python scripts/policy_e2e_sdk.py "$@"); }
web() { (cd "$root/apps/web" && E2E_POLICY_API_URL="$API" E2E_WORKSPACE="$WS" pnpm exec playwright test e2e/policy-real.spec.ts "$@"); }
mkdir -p "$root/docs/screenshots/phase-14"

check provision
check publish-starter

if stage 1; then
  step "gate 1: the protected demo action is paused until a human decides (real SDK, API, worker, browser)"
  check gate1-start approve            # runs the demo in the background; the expiry variant runs in parallel (nobody decides)
  check gate1-start expiry
  web --grep "@gate1-approve"
  check gate1-finish approve
  check gate1-start deny
  web --grep "@gate1-deny"
  check gate1-finish deny
  check gate1-finish expiry             # ~70 s after it started; most of that time has already passed
fi
if stage 2; then step "gate 2: a replayed approval is rejected in every way"; check gate2; fi
if stage 4; then step "gate 4: failure modes hold (outage, timeouts, per-class defaults, cached table, expiry fallbacks)"; sdk_driver gate4; fi
if stage 5; then
  step "gate 5: the state machine is atomic under real concurrency; illegal SQL as the runtime role fails"
  check gate5
fi
if stage 7; then step "gate 7: SDK safety (SIGINT during a wait, only documented exceptions, bounded waits, stdlib only)"; check gate7; fi
if stage 6; then
  step "gate 6: authorization (API) then the browser as every role"
  check gate6-prepare
  web --grep "@gate6"
  check gate6-verify
fi
if stage 8; then
  step "gate 8: dry run compared with an independent evaluation; decision and ingestion latency"
  check gate8
fi
# `POLICY_E2E_STAGES=probe` only (a development aid): when a wait ends relative to the expiry, and when the server stamps it
if [[ ",${POLICY_E2E_STAGES:-}," == *",probe,"* ]]; then check expiry-probe; fi
if stage 9; then
  step "real-stack UI checks: editor messages, version conflict, dry-run links, headline time, roles, axe, screenshots"
  check ui-prepare
  web --grep "@ui"
fi
step "final scan: no planted secret, key token or intent value in any column, artifact file or log"
check scan final
printf '\nPOLICY-E2E PASSED\n'
