#!/usr/bin/env bash
# Security observability E2E (Phase 13, gates 1-7): a dedicated database (abb_security_e2e), an API on :8190, a second API on
# :8192 with a deliberately tiny scan budget (same database, same artifact directory), a worker, the DEVELOPMENT-ONLY fake
# OIDC provider on :8191 and the built web server on :3190 (started by Playwright).
#   1. a PLANTED run through the real Python SDK and its coding helpers: exactly six findings, linked to events, no content,
#      byte-identical for owner, developer, viewer and a read key; billing gets 403
#   2. an "SDK" that skips redaction (raw HTTP): secrets are redacted before storage; a scan of every text/jsonb column of the
#      database, the artifact directory and the logs finds none; scan failure and an oversized body fail closed
#   3. FULL / METADATA_ONLY / DISABLED projects store what they should
#   4. an ordinary SDK coding run has no finding
#   6. deleted findings come back from `rebuild-findings`; a mute or an allowlist change re-analyses without a rebuild
#   7. ingestion latency (100-event batches) and 10,000-event run detection stay inside a generous bound
# Then Playwright drives a real browser through the OIDC flow as an owner, a viewer and a billing person.
# Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`. Never touches the shared dev/test databases.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${SECURITY_E2E_DB_NAME:-abb_security_e2e}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
export ABB_FINDINGS_DELAY_SECONDS=1
export ABB_SECURITY_REANALYSE_COOLDOWN_SECONDS=0   # the check changes one project's settings several times in a row
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000     # the benchmark measures the pipeline, not the default throttle
PORT="${SECURITY_E2E_API_PORT:-8190}"; API="http://localhost:$PORT"
STRICT_PORT="${SECURITY_E2E_STRICT_PORT:-8192}"; STRICT_API="http://localhost:$STRICT_PORT"
OIDC_PORT="${SECURITY_E2E_OIDC_PORT:-8191}"
export ABB_WEB_ORIGIN="http://localhost:3190"       # fixed: playwright.config.ts starts the web server there
export ABB_ENVIRONMENT=development                  # http issuers and dev sessions exist only here and in test
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT"
export ABB_OIDC_CLIENT_ID="abb-dev"
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  # SECURITY_E2E_LOG_DIR keeps the server logs of a failed run (they hold no payload bodies or secrets, INV-5).
  if [ -n "${SECURITY_E2E_LOG_DIR:-}" ]; then mkdir -p "$SECURITY_E2E_LOG_DIR" && cp "$WORK"/*.log "$SECURITY_E2E_LOG_DIR"/ 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "SECURITY-E2E FAILED: $*" >&2; exit 1; }
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

step "start the fake identity provider (:$OIDC_PORT), the API (:$PORT), a strict-budget API (:$STRICT_PORT) and a worker"
for u in "$API/readyz" "$STRICT_API/readyz" "http://localhost:$OIDC_PORT/.well-known/openid-configuration"; do
  if curl -sf "$u" >/dev/null 2>&1; then fail "something is already serving $u; stop it or set SECURITY_E2E_API_PORT / _STRICT_PORT / _OIDC_PORT"; fi
done
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
# A scan budget no real scan can meet (a nanosecond) and a 2 KiB artifact cap: every payload and artifact must fail closed.
ABB_SECURITY_SCAN_EVENT_MAX_SECONDS=0.000000001 ABB_SECURITY_SCAN_ARTIFACT_MAX_SECONDS=0.000000001 \
  ABB_SECURITY_SCAN_ARTIFACT_MAX_BYTES=2048 \
  uv run uvicorn abb_api.main:app_from_env --factory --port "$STRICT_PORT" >"$WORK/api-strict.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 60); do
  curl -sf "$API/readyz" >/dev/null && curl -sf "$STRICT_API/readyz" >/dev/null \
    && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null && break
  sleep 0.5
done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
curl -sf "$STRICT_API/readyz" >/dev/null || fail "strict API did not become ready: $(tail -5 "$WORK/api-strict.log")"
curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null || fail "fake provider did not start: $(tail -5 "$WORK/oidc.log")"

step "provision a workspace, five projects, people for every role and keys (generated, local-only; secrets never printed)"
suffix="$(date +%s)$RANDOM"
WS="security-e2e-$suffix"
OWNER="owner-$suffix@security.test"; DEV="developer-$suffix@security.test"; VIEWER="viewer-$suffix@security.test"; BILLING="billing-$suffix@security.test"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Security E2E" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Full --slug full --capture-mode full >/dev/null
cli create-project --workspace "$WS" --name Meta --slug meta --capture-mode metadata_only >/dev/null
cli create-project --workspace "$WS" --name Off --slug off --capture-mode disabled >/dev/null
cli create-project --workspace "$WS" --name Strict --slug strict --capture-mode full >/dev/null
cli create-project --workspace "$WS" --name Bench --slug bench --capture-mode full >/dev/null
for pair in "$OWNER:OWNER" "$DEV:DEVELOPER" "$VIEWER:VIEWER" "$BILLING:BILLING"; do
  cli add-member --workspace "$WS" --email "${pair%%:*}" --role "${pair##*:}" >/dev/null
done
wkey() { cli create-key --workspace "$WS" --project "$1" --scopes events:write artifacts:write --name "security-$1"; }
E2E_KEY_FULL="$(wkey full)"; E2E_KEY_META="$(wkey meta)"; E2E_KEY_OFF="$(wkey off)"; E2E_KEY_STRICT="$(wkey strict)"; BENCH_KEY="$(wkey bench)"
E2E_READ_KEY="$(cli create-key --workspace "$WS" --scopes runs:read --name security-script-read)"   # scripts only; never given to the web
for k in "$E2E_KEY_FULL" "$E2E_KEY_META" "$E2E_KEY_OFF" "$E2E_KEY_STRICT" "$BENCH_KEY" "$E2E_READ_KEY"; do [[ "$k" == abb_live_* ]] || fail "key creation"; done
session() { ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$1" --hours 2; }
E2E_OWNER_SESSION="$(session "$OWNER")"; E2E_DEVELOPER_SESSION="$(session "$DEV")"
E2E_VIEWER_SESSION="$(session "$VIEWER")"; E2E_BILLING_SESSION="$(session "$BILLING")"
[ -n "$E2E_OWNER_SESSION" ] && [ -n "$E2E_DEVELOPER_SESSION" ] && [ -n "$E2E_VIEWER_SESSION" ] && [ -n "$E2E_BILLING_SESSION" ] || fail "could not mint sessions"
printf '%s' "$BENCH_KEY" >"$WORK/bench.key"; printf '%s' "$E2E_READ_KEY" >"$WORK/read.key"; chmod 600 "$WORK/bench.key" "$WORK/read.key"

# Planted values: unique per run (the database is reused), shaped like the real thing so the scanner would catch them.
rand() { LC_ALL=C tr -dc "$1" </dev/urandom | head -c "$2" || true; }
export E2E_PLANT="PLANTE2E${suffix}"
export E2E_AWS="AKIA$(rand 'A-Z0-9' 16)"; export E2E_AWS2="AKIA$(rand 'A-Z0-9' 16)"
export E2E_PEM_BODY="MIIEowIBAAKCAQEA$(rand 'A-Za-z0-9' 40)"; export E2E_PEM_BODY2="MIIEowIBAAKCAQEA$(rand 'A-Za-z0-9' 40)"
export E2E_PASSWORD="pw-$(rand 'a-z0-9' 14)-e2e"
export E2E_API="$API" E2E_STRICT_API="$STRICT_API" E2E_WORKSPACE="$WS" E2E_ORIGIN="$ABB_WEB_ORIGIN" E2E_STATE="$WORK/state.json"
export E2E_OWNER_SESSION E2E_DEVELOPER_SESSION E2E_VIEWER_SESSION E2E_BILLING_SESSION E2E_READ_KEY
export E2E_KEY_FULL E2E_KEY_META E2E_KEY_OFF E2E_KEY_STRICT E2E_ARTIFACT_DIR="$ABB_ARTIFACT_DIR"
export E2E_LOGS="$WORK/api.log:$WORK/api-strict.log:$WORK/worker.log"
: >"$WORK/state.json"
check() { uv run python "$root/scripts/security_e2e_check.py" "$@"; }
# The SDK process gets a clean environment: its coding helpers mask the host's environment values, so the planted secrets travel
# in a file, not in a variable.
python3 -c 'import json,os; print(json.dumps({"plant": os.environ["E2E_PLANT"], "aws": os.environ["E2E_AWS"], "pem_body": os.environ["E2E_PEM_BODY"]}))' >"$WORK/plant.json"
chmod 600 "$WORK/plant.json"
sdk() { (cd "$root" && env -i HOME="$HOME" PATH="$PATH" BLACKBOX_API_KEY="$E2E_KEY_FULL" BLACKBOX_ENDPOINT="$API" \
  uv run --project packages/sdk-python python scripts/security_e2e_plant.py "$1" "$WORK/plant.json" | tail -1); }
run_id_of() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["run_id"])'; }

step "the owner allows one host for the project (through the settings API), so the planted off-list host is unexpected"
check put-allowlist

step "gate 1: a planted run through the real SDK and coding helpers"
PLANTED_RUN="$(sdk planted | run_id_of)"; [ -n "$PLANTED_RUN" ] || fail "the planted run was not recorded"
check save planted_run "$PLANTED_RUN"
step "gate 4: an ordinary coding run through the same SDK, same project and allowlist"
BENIGN_RUN="$(sdk benign | run_id_of)"; [ -n "$BENIGN_RUN" ] || fail "the ordinary run was not recorded"
check save benign_run "$BENIGN_RUN"
check gate1-verify
check gate4-benign

step "gate 3: three projects, one payload-bearing run each (FULL / METADATA_ONLY / DISABLED)"
check gate3-modes
step "gate 2: an SDK that skips redaction (raw HTTP), then a scan of the whole database, the artifact directory and the logs"
check gate2-backstop
step "gate 6: findings rebuilt from events; settings changes re-analyse recent runs without a rebuild"
check gate6-rebuild

step "gate 7: ingestion latency and 10,000-event detection (docs/benchmarks/phase-13-security.md)"
uv run python "$root/scripts/security_bench.py" --api "$API" --key-file "$WORK/bench.key" --read-key-file "$WORK/read.key" --assert
step "final scan after everything else (including the benchmark's events)"
check scan final

step "Playwright: a real browser through the OIDC flow as an owner, a viewer and a billing person"
cd "$root/apps/web"
mkdir -p "$root/docs/screenshots/phase-13"
PLANTED_JSON="$(python3 -c 'import json,os; print(json.dumps({"plant": os.environ["E2E_PLANT"], "aws": os.environ["E2E_AWS"], "pem": os.environ["E2E_PEM_BODY"], "host": "exfil-" + os.environ["E2E_PLANT"], "dir": "deploy-" + os.environ["E2E_PLANT"]}))')"
E2E_SECURITY_API_URL="$API" E2E_WORKSPACE="$WS" E2E_OWNER_EMAIL="$OWNER" E2E_VIEWER_EMAIL="$VIEWER" E2E_BILLING_EMAIL="$BILLING" \
  E2E_PLANTED_RUN="$PLANTED_RUN" E2E_BENIGN_RUN="$BENIGN_RUN" E2E_PLANTED="$PLANTED_JSON" \
  pnpm exec playwright test e2e/security-real.spec.ts
printf '\nSECURITY-E2E PASSED\n'
