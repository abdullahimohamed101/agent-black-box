#!/usr/bin/env bash
# Observability E2E (Phase 19c step 6, gates 1-5 and 8 of D9): a REAL stack on a dedicated database (abb_p19c_obs): migrations as the
# owner, an API and a worker as a least-privilege login role (exactly as production runs them) with a metrics token in a 0600 file,
# and, per stage, further processes with their own (deliberately broken or tight) settings. Everything is asserted as exact counter
# deltas, label sets, status codes and presence/absence; "eventually" facts (Prometheus having scraped, a rule having evaluated, a
# span having been exported) use bounded polls with generous deadlines. No wall-clock threshold is asserted anywhere; timings are
# recorded for information only.
#   1. /metrics: 404 without a token configured; one 401 for a wrong or missing token; 200 with the Prometheus text type, no-store,
#      hidden from OpenAPI; parses; bounded series after a hostile flood; no tenant text; the API and the worker serve the DB gauges
#   2. metrics MOVE with the right traffic: ingest, every refusal reason, quota, throttle, a dead-lettered job, a failing alert
#      delivery, retention and backup textfiles, session revocations; the SLI ratio equals what was driven
#   3. readiness and the breaker with deliberately broken processes: database down, a database proxy outage and recovery, schema
#      behind, artifact directory not writable, a role that became privileged in production (the process keeps running)
#   4. logs: every process ran at DEBUG; every line is JSON with the required fields; no sentinel, secret, token, cookie, key,
#      address, e-mail or DSN password in any of them; no unexpected Traceback or ERROR line
#   5. REAL Prometheus and Grafana from the pinned images (promtool check config / rules; both targets UP; every rule loaded and
#      healthy; recording rules produce series; every rule and dashboard expression executes; the dashboard is provisioned; the
#      AbbTargetDown alert FIRES when the worker is stopped), in containers of a private compose project that is removed afterwards
#   6. tracing (the `otel` extra in a separate environment): spans reach a local OTLP receiver for a request and a job, the job span
#      links to the request trace, only allow-listed attributes, no sentinel in any body, a dead collector never breaks ingestion
#   7. cost: ingestion latency with metrics and tracing off against on (docs/benchmarks/phase-19c-obs-e2e.md; informational)
# Needs Postgres on 5433 (the compose `postgres` service or the CI service), Docker (stage 5), `uv` (stages 6-7) and a `.env` or
# `.env.example`. Drops and recreates its own databases and login role on every run; never touches the shared dev/test databases and
# unsets every variable that would let a migration or a tool change a shared role or reach another database.
# OBS_E2E_STAGES=1,2 limits the stages (development aid; CI runs them all); OBS_E2E_ROUNDS sets the benchmark rounds (default 5);
# OBS_E2E_LOG_DIR keeps the process logs (no payload bodies or secrets: INV-5); OBS_E2E_HOLD=<s> holds the stack (development aid).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; if [ -f ./.env ]; then . ./.env; else . ./.env.example; fi; set +a
# Nothing below may reach the developer's database or alter a cluster-wide role: `alembic upgrade` sets the abb_runtime and
# abb_retention passwords when these are present, and the tools connect wherever these point.
unset ABB_RUNTIME_DB_PASSWORD ABB_RETENTION_DB_PASSWORD ABB_RETENTION_DATABASE_URL ABB_MIGRATION_DATABASE_URL ABB_BACKUP_DATABASE_URL \
      ABB_RESTORE_DATABASE_URL ABB_ROTATION_DATABASE_URL ABB_TEST_DATABASE_URL ABB_METRICS_TOKEN ABB_METRICS_TEXTFILE ABB_WORKER_METRICS_PORT \
      ABB_WORKER_METRICS_BIND ABB_OTEL_ENABLED ABB_OTEL_SAMPLE_RATIO ABB_ARTIFACT_DIR ABB_RETENTION_MODE ABB_ALLOW_PRIVILEGED_DB_ROLE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_HEADERS OTEL_EXPORTER_OTLP_TRACES_ENDPOINT ABB_OIDC_ISSUER ABB_OIDC_CLIENT_ID \
      ABB_OIDC_CLIENT_SECRET ABB_SMTP_HOST ABB_SMTP_PORT ABB_ALERTS_DELIVERY_ENABLED
DB_NAME="${OBS_E2E_DB_NAME:-abb_p19c_obs}"
DB_BEHIND="${DB_NAME}_behind"
ROLE_NAME="abb_p19c_obs_runtime"
OWNER_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
RUNTIME_PASSWORD="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 32 || true)"
METRICS_TOKEN="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 48 || true)"
API_PORT="${OBS_E2E_API_PORT:-8240}"; WORKER_METRICS_PORT=$((API_PORT + 9)); WEB_PORT=$((API_PORT + 60))
export OBS_E2E_API_PORT="$API_PORT" OBS_E2E_WORKER_METRICS_PORT="$WORKER_METRICS_PORT"
export ABB_DATABASE_URL="$OWNER_URL"                   # provisioning and migrations run as the owner; the serving processes get the runtime role
export ABB_WEB_ORIGIN="http://localhost:$WEB_PORT"     # the cookie origin; nothing listens there (no UI is driven)
export ABB_ENVIRONMENT=development                     # dev sessions exist only here and in test
export ABB_LOG_LEVEL=DEBUG                             # gate 4: every process logs at the most verbose level
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_FINDINGS_MIN_INTERVAL_SECONDS=0
export ABB_AUDIT_DENIALS_PER_MINUTE=1000
export ABB_METRICS_REQUESTS_PER_MINUTE=100000          # many scrapes per second; the 429 bucket is covered by tests/obs
# Alerting tuned so a failing delivery reaches its dead letter quickly: the relay is a closed loopback port, so every attempt fails.
export ABB_ALERTS_DELIVERY_ENABLED=true ABB_ALERTS_SWEEP_INTERVAL_SECONDS=1 ABB_ALERTS_REQUEST_TIMEOUT_SECONDS=2
export ABB_ALERTS_BACKOFF_BASE_SECONDS=1 ABB_ALERTS_BACKOFF_MAX_SECONDS=2 ABB_ALERTS_MAX_ATTEMPTS=2
export ABB_SMTP_HOST=127.0.0.1 ABB_SMTP_PORT=$((API_PORT + 19)) ABB_SMTP_STARTTLS=false ABB_SMTP_FROM=alerts@abb.example.test
mkdir -p "$root/.local"
# Under the checkout, not the system temp directory: the Prometheus and Grafana containers bind-mount files from it, and a
# container VM (Colima, Docker Desktop) shares the user's home but not /var/folders.
WORK="$(mktemp -d "$root/.local/obs-e2e.XXXXXX")"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
umask 077
printf '%s' "$METRICS_TOKEN" >"$WORK/metrics-token"; chmod 600 "$WORK/metrics-token"
export ABB_METRICS_TOKEN="$METRICS_TOKEN"
stage() { [ -z "${OBS_E2E_STAGES:-}" ] || [[ ",$OBS_E2E_STAGES," == *",$1,"* ]]; }
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  if [ -f "$WORK/spawned.pids" ]; then while read -r g; do [ -n "$g" ] && kill -TERM -- "-$g" 2>/dev/null || true; done <"$WORK/spawned.pids"; fi
  # Only what this script created: its compose project (containers, network, volume), never an image.
  if [ -f "$WORK/compose.project" ]; then
    docker compose -p "$(cat "$WORK/compose.project")" -f "$root/docker-compose.yml" -f "$WORK/prom/compose.override.yml" --profile observability \
      down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  # The throwaway login role is cluster-level: drop it once its databases are gone (best effort; the next run drops it again).
  (cd "$root/apps/api" && ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" DB_BEHIND="$DB_BEHIND" ROLE_NAME="$ROLE_NAME" .venv/bin/python - <<'PY' >/dev/null 2>&1
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    for name in (os.environ["DB_NAME"], os.environ["DB_BEHIND"]):
        await conn.execute(f'drop database if exists "{name}" with (force)')
    await conn.execute(f'drop role if exists "{os.environ["ROLE_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
  ) || true
  if [ -n "${OBS_E2E_LOG_DIR:-}" ]; then
    mkdir -p "$OBS_E2E_LOG_DIR" && for f in "$WORK"/*.log "$WORK"/results.jsonl; do cp "$f" "$OBS_E2E_LOG_DIR"/ 2>/dev/null || true; done
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "OBS-E2E FAILED: $*" >&2; exit 1; }
for p in $API_PORT $WORKER_METRICS_PORT; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then fail "something is already listening on :$p"; fi
done
cd apps/api
PY=.venv/bin/python
[ -x "$PY" ] || fail "apps/api/.venv is missing (cd apps/api && uv sync --frozen)"

step "recreate the dedicated databases $DB_NAME and $DB_BEHIND and the throwaway runtime login role, migrate to head (as the owner)"
ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" DB_BEHIND="$DB_BEHIND" ROLE_NAME="$ROLE_NAME" $PY - <<'PY' || fail "database setup"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    for name in (os.environ["DB_NAME"], os.environ["DB_BEHIND"]):
        await conn.execute(f'drop database if exists "{name}" with (force)')
    await conn.execute(f'drop role if exists "{os.environ["ROLE_NAME"]}"')
    await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
$PY -m alembic upgrade head >/dev/null 2>"$WORK/migrate.err" || fail "migrations: $(tail -3 "$WORK/migrate.err")"
ADMIN_URL="$OWNER_URL" ROLE_NAME="$ROLE_NAME" RUNTIME_PASSWORD="$RUNTIME_PASSWORD" $PY - <<'PY' || fail "runtime role"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    # LOGIN role that inherits the migration-created abb_runtime privileges: what docker-compose and the tests do.
    await conn.execute(f'create role "{os.environ["ROLE_NAME"]}" login password \'{os.environ["RUNTIME_PASSWORD"]}\' in role abb_runtime')
    await conn.close()
asyncio.run(main())
PY
export E2E_OWNER_DB_URL="$OWNER_URL"
export E2E_RUNTIME_DB_URL="$(OWNER_URL="$OWNER_URL" ROLE_NAME="$ROLE_NAME" RUNTIME_PASSWORD="$RUNTIME_PASSWORD" $PY - <<'PY'
import os
from sqlalchemy.engine import make_url
print(make_url(os.environ["OWNER_URL"]).set(username=os.environ["ROLE_NAME"], password=os.environ["RUNTIME_PASSWORD"]).render_as_string(hide_password=False))
PY
)"
[ -n "$E2E_RUNTIME_DB_URL" ] || fail "could not build the runtime URL"
export E2E_DB_NAME="$DB_NAME" E2E_DB_BEHIND="$DB_BEHIND" E2E_ROLE_NAME="$ROLE_NAME"

if stage 6 || stage 7; then
  step "the tracing environment: a separate virtualenv with the otel extra (the default environment stays clean)"
  export UV_PROJECT_ENVIRONMENT="$WORK/otel-venv"
  { uv sync --frozen --no-dev --extra otel >"$WORK/otel-sync.log" 2>&1 || uv sync --frozen --no-dev --extra otel --offline >>"$WORK/otel-sync.log" 2>&1; } \
    || fail "could not create the otel environment: $(tail -3 "$WORK/otel-sync.log")"
  unset UV_PROJECT_ENVIRONMENT
  "$WORK/otel-venv/bin/python" -c "import opentelemetry.sdk, opentelemetry.proto" || fail "the otel environment lacks the extra"
  export E2E_OTEL_PYTHON="$WORK/otel-venv/bin/python"
fi

step "start the API (:$API_PORT, metrics token) and the worker (metrics on :$WORKER_METRICS_PORT) as the least-privilege role"
ABB_DATABASE_URL="$E2E_RUNTIME_DB_URL" $PY -m uvicorn abb_api.main:app_from_env --factory --host 127.0.0.1 --port "$API_PORT" >"$WORK/api-main.log" 2>&1 & pids+=($!)
echo $! >"$WORK/api-main.pid"
ABB_DATABASE_URL="$E2E_RUNTIME_DB_URL" ABB_WORKER_METRICS_PORT="$WORKER_METRICS_PORT" $PY -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
echo $! >"$WORK/worker.pid"
for _ in $(seq 1 80); do
  curl -sf "http://127.0.0.1:$API_PORT/readyz" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$API_PORT/readyz" >/dev/null || fail "the API did not become ready: $(tail -5 "$WORK/api-main.log")"
for _ in $(seq 1 40); do
  curl -sf -H "Authorization: Bearer $METRICS_TOKEN" "http://127.0.0.1:$WORKER_METRICS_PORT/metrics" >/dev/null && break
  sleep 0.5
done
curl -sf -H "Authorization: Bearer $METRICS_TOKEN" "http://127.0.0.1:$WORKER_METRICS_PORT/metrics" >/dev/null || fail "the worker did not serve metrics: $(tail -5 "$WORK/worker.log")"

step "provision the world (generated, local-only; secrets never printed)"
suffix="$(date +%s)$RANDOM"
export E2E_WORKSPACE="obs-$suffix" E2E_WORKSPACE2="obs-other-$suffix" E2E_SUFFIX="$suffix"
export E2E_ROOT="$root" E2E_WORK="$WORK" E2E_STATE="$WORK/state.json" E2E_RESULTS="$WORK/results.jsonl" E2E_ORIGIN="$ABB_WEB_ORIGIN"
export E2E_OIDC_PORT=0 E2E_API_PORTS="$API_PORT" E2E_METRICS_TOKEN_FILE="$WORK/metrics-token"
: >"$WORK/state.json"; : >"$WORK/results.jsonl"; chmod 600 "$WORK/state.json"
failed=()
check() { $PY "$root/scripts/obs_e2e_check.py" "$@" || failed+=("$*"); }
check provision
[ "${#failed[@]}" -eq 0 ] || { echo "OBS-E2E FAILED: provisioning (${failed[*]})" >&2; exit 1; }

if stage 1; then step "gate 1: /metrics is authenticated, absent without a token, bounded, free of tenant text, served by API and worker"; check gate1; fi
if stage 2; then step "gate 2: every catalogue family moves by exactly the traffic driven"; check gate2; fi
if stage 3; then step "gate 3: readiness and the database breaker with deliberately broken processes"; check gate3; fi
if stage 5; then step "gate 5: real Prometheus and Grafana (pinned images): config, rules, scrape, every expression, the dashboard, a firing alert"; check gate5; fi
if stage 6; then step "gate 6: tracing: spans for a request and a job, the link, the allow-list, no sentinel, a dead collector"; check gate6; fi
if stage 7; then step "gate 7: cost of metrics and tracing on against off (informational)"; check gate7; fi
[ -z "${OBS_E2E_HOLD:-}" ] || { step "holding the stack for ${OBS_E2E_HOLD}s (development aid)"; sleep "$OBS_E2E_HOLD"; }
step "gate 4 and summary: every process log scanned"
check summary || true
if [ "${#failed[@]}" -gt 0 ]; then printf 'OBS-E2E FAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
printf '\nOBS-E2E PASSED\n'
