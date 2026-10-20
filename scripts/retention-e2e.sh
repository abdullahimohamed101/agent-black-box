#!/usr/bin/env bash
# Retention E2E (Phase 19b step 6, gates 1-8 of D6): a REAL stack on a dedicated database (abb_p19b6_retention): migrations as the owner,
# the API and the worker as a least-privilege login (member of abb_runtime), the retention job as ITS OWN login (member of
# abb_retention, credentials in a 0600 file that neither the API nor the worker can see), a real artifact directory, the DEVELOPMENT-ONLY
# fake OIDC provider on :8241 and the built web server on :3230 (started by Playwright). Data goes in through the REAL ingestion API
# (events, artifacts) and is aged by the owner's SQL (events are immutable for the API, so the owner is the test harness).
#   1. dry run: reports with the right counts (computed independently), NOTHING deleted (rows of every table, content, files)
#   2. enforce: policy through the API (role-gated, floor, audited, isolated), the documented order (observed in the tombstones), trace
#      states served by the API (409 TRACE_EXPIRED), the quota counter decreased by exactly the deleted bytes, rollups, tombstones
#   3. crash safety: SIGKILL while the job waits on a row lock the harness holds (an OBSERVED state, no sleeps), rerun, same end state
#   4. kill switch (SIGTERM, the mode file) and the per-tick bounds
#   5. privileges over real connections (runtime and retention logins) and no retention credential in the API's environment
#   6. purge-project and purge-workspace: dry run, wrong confirmation, zero rows left in every table, siblings byte-identical, uniform 401
#   7. a real browser: the Retention page against the real API and a run page in each trace state (axe, console)
#   8. cost: ingestion latency idle vs while enforcement runs in the background (INFORMATIONAL: docs/benchmarks/phase-19b-retention-e2e.md)
# Only deterministic facts are asserted: counts, states, orders observed in the database, exit codes. No wall-clock threshold.
# Needs Postgres on 5433 and `pnpm --filter @abb/web build`. Drops and recreates its own database on every run; never touches the shared
# dev/test databases. RETENTION_E2E_STAGES=1,2 limits the stages (development aid; CI runs them all); RETENTION_E2E_LOG_DIR keeps the
# process logs (no payload bodies or secrets: INV-5); RETENTION_E2E_ROUNDS sets the cost rounds (default 5).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; if [ -f ./.env ]; then . ./.env; else . ./.env.example; fi; set +a
# Nothing from the developer's .env may redirect the migrations, the roles or the retention job.
unset ABB_RETENTION_DATABASE_URL ABB_MIGRATION_DATABASE_URL ABB_RETENTION_MODE ABB_BACKUP_DATABASE_URL ABB_RESTORE_DATABASE_URL ABB_ROTATION_DATABASE_URL \
      ABB_RETENTION_ARTIFACTS_DAYS ABB_RETENTION_TRACE_DAYS ABB_RETENTION_RUN_DAYS ABB_RETENTION_AUDIT_DAYS \
      ABB_RUNTIME_DB_PASSWORD ABB_RETENTION_DB_PASSWORD
# These scripts DROP and recreate their own databases and roles on the server ABB_DATABASE_URL names: refuse anything but a local one
# unless the operator says, in so many words, that it is meant (a shared or production server would lose its `abb_p19b6_*` objects).
case "$(printf '%s' "$ABB_DATABASE_URL" | sed -E 's#^[a-z+]+://([^@/]*@)?(\[[^]]*\]|[^:/?]*).*#\2#')" in
  localhost|127.0.0.1|'[::1]') ;;
  *) [ "${ABB_E2E_ALLOW_REMOTE_DATABASE:-}" = "1" ] || { echo "$(basename "$0"): ABB_DATABASE_URL is not a local database; set ABB_E2E_ALLOW_REMOTE_DATABASE=1 to run against it knowingly" >&2; exit 2; } ;;
esac
DB_NAME="${RETENTION_E2E_DB_NAME:-abb_p19b6_retention}"
RUNTIME_ROLE="abb_p19b6_ret_runtime"; RETENTION_ROLE="abb_p19b6_ret_job"
OWNER_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
rand() { LC_ALL=C tr -dc "$1" </dev/urandom | head -c "$2" || true; }
RUNTIME_PASSWORD="$(rand 'a-f0-9' 32)"; RETENTION_PASSWORD="$(rand 'a-f0-9' 32)"
API_PORT="${RETENTION_E2E_API_PORT:-8240}"; OIDC_PORT="${RETENTION_E2E_OIDC_PORT:-8241}"; WEB_PORT=3230
export ABB_WEB_ORIGIN="http://localhost:$WEB_PORT"     # fixed: the fake provider's redirect URI and the cookie origin
export ABB_ENVIRONMENT=development                     # http issuers and dev sessions exist only here and in test
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT" ABB_OIDC_CLIENT_ID="abb-dev"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_FINDINGS_MIN_INTERVAL_SECONDS=0
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000 ABB_RATE_LIMIT_BURST_EVENTS=1000000
export ABB_AUDIT_DENIALS_PER_MINUTE=1000
# The API only DISPLAYS the installation mode; the retention job reads its own. This stack enforces, so the page says so.
export ABB_RETENTION_MODE=enforce
WORK="$(mktemp -d)"; pids=()
ARTIFACTS="$WORK/artifacts"; mkdir -p "$ARTIFACTS"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  (cd "$root/apps/api" && ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" R1="$RUNTIME_ROLE" R2="$RETENTION_ROLE" .venv/bin/python - <<'PY' >/dev/null 2>&1
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await conn.execute(f'drop database if exists "{os.environ["DB_NAME"]}" with (force)')
    for role in (os.environ["R1"], os.environ["R2"]):
        await conn.execute(f'drop role if exists "{role}"')
    await conn.close()
asyncio.run(main())
PY
  ) || true
  # `oidc.log` is NOT kept: the development-only fake provider's log can hold authorization codes and id tokens.
  if [ -n "${RETENTION_E2E_LOG_DIR:-}" ]; then
    mkdir -p "$RETENTION_E2E_LOG_DIR" && for f in "$WORK"/*.log "$WORK"/results.jsonl; do [ "$(basename "$f")" = "oidc.log" ] || cp "$f" "$RETENTION_E2E_LOG_DIR"/ 2>/dev/null || true; done
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "RETENTION-E2E FAILED: $*" >&2; exit 1; }
stage() { [ -z "${RETENTION_E2E_STAGES:-}" ] || [[ ",$RETENTION_E2E_STAGES," == *",$1,"* ]]; }
for p in $API_PORT $OIDC_PORT $WEB_PORT; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then fail "something is already listening on :$p"; fi
done
cd apps/api
PY=.venv/bin/python
[ -x "$PY" ] || fail "apps/api/.venv is missing (cd apps/api && uv sync --frozen)"

step "recreate the dedicated database $DB_NAME and the two login roles, migrate to head (as the owner)"
ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" R1="$RUNTIME_ROLE" R2="$RETENTION_ROLE" $PY - <<'PY' || fail "database setup"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await conn.execute(f'drop database if exists "{os.environ["DB_NAME"]}" with (force)')
    for role in (os.environ["R1"], os.environ["R2"]):
        await conn.execute(f'drop role if exists "{role}"')
    await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
ABB_DATABASE_URL="$OWNER_URL" $PY -m alembic upgrade head >/dev/null 2>"$WORK/migrate.err" || fail "migrations: $(tail -3 "$WORK/migrate.err")"
ADMIN_URL="$OWNER_URL" R1="$RUNTIME_ROLE" P1="$RUNTIME_PASSWORD" R2="$RETENTION_ROLE" P2="$RETENTION_PASSWORD" $PY - <<'PY' || fail "login roles"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    # LOGIN roles that inherit the migration-created privileges: what docker-compose and the tests do for abb_runtime / abb_retention.
    await conn.execute(f'create role "{os.environ["R1"]}" login password \'{os.environ["P1"]}\' in role abb_runtime')
    await conn.execute(f'create role "{os.environ["R2"]}" login password \'{os.environ["P2"]}\' in role abb_retention')
    await conn.close()
asyncio.run(main())
PY
urlfor() { OWNER_URL="$OWNER_URL" U="$1" P="$2" $PY - <<'PY'
import os
from sqlalchemy.engine import make_url
print(make_url(os.environ["OWNER_URL"]).set(username=os.environ["U"], password=os.environ["P"]).render_as_string(hide_password=False))
PY
}
RUNTIME_URL="$(urlfor "$RUNTIME_ROLE" "$RUNTIME_PASSWORD")"
# The retention login's URL lives in a private file the check reads: it is never in an environment the API or worker inherits.
(umask 077; urlfor "$RETENTION_ROLE" "$RETENTION_PASSWORD" >"$WORK/retention.url")
[ -n "$RUNTIME_URL" ] && [ -s "$WORK/retention.url" ] || fail "could not build the role URLs"

step "start the fake identity provider (:$OIDC_PORT), the API (:$API_PORT) and a worker, both as the runtime login (no retention credential)"
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
ABB_DATABASE_URL="$RUNTIME_URL" ABB_ARTIFACT_DIR="$ARTIFACTS" $PY -m uvicorn abb_api.main:app_from_env --factory --host 127.0.0.1 --port "$API_PORT" >"$WORK/api.log" 2>&1 & api_pid=$!; pids+=($api_pid)
ABB_DATABASE_URL="$RUNTIME_URL" ABB_ARTIFACT_DIR="$ARTIFACTS" $PY -m abb_api.worker >"$WORK/worker.log" 2>&1 & worker_pid=$!; pids+=($worker_pid)
echo "$api_pid" >"$WORK/api.pid"; echo "$worker_pid" >"$WORK/worker.pid"
for _ in $(seq 1 80); do
  curl -sf "http://127.0.0.1:$API_PORT/readyz" >/dev/null && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$API_PORT/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null || fail "fake provider did not start: $(tail -5 "$WORK/oidc.log")"

step "provision the world (generated, local-only; secrets never printed): three workspaces, people, projects and keys"
suffix="$(date +%s)$RANDOM"
export E2E_WORKSPACE="retention-$suffix" E2E_WORKSPACE2="retention-other-$suffix" E2E_SUFFIX="$suffix"
export E2E_ROOT="$root" E2E_WORK="$WORK" E2E_STATE="$WORK/state.json" E2E_RESULTS="$WORK/results.jsonl" E2E_ORIGIN="$ABB_WEB_ORIGIN"
export E2E_OIDC_PORT="$OIDC_PORT" E2E_API_URL="http://127.0.0.1:$API_PORT" E2E_ARTIFACT_DIR="$ARTIFACTS" E2E_RETENTION_URL_FILE="$WORK/retention.url"
# The owner and runtime URLs are exported only now: the API and the worker were started without them.
export E2E_OWNER_DB_URL="$OWNER_URL" E2E_RUNTIME_DB_URL="$RUNTIME_URL"
: >"$WORK/state.json"; : >"$WORK/results.jsonl"; chmod 600 "$WORK/state.json"
failed=()
check() { $PY "$root/scripts/retention_e2e_check.py" "$@" || failed+=("$*"); }
web() { (cd "$root/apps/web" && E2E_RETENTION_API_URL="$E2E_API_URL" E2E_WEB_PORT="$WEB_PORT" pnpm exec playwright test e2e/retention-real.spec.ts "$@") || failed+=("web $*"); }
check provision
[ "${#failed[@]}" -eq 0 ] || { echo "RETENTION-E2E FAILED: provisioning (${failed[*]})" >&2; exit 1; }
export E2E_OWNER_EMAIL="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["people"]["owner"])')"
step "seed: runs with events and artifacts through the real ingestion API, then aged by the owner's SQL"
check seed
[ "${#failed[@]}" -eq 0 ] || { echo "RETENTION-E2E FAILED: seeding (${failed[*]})" >&2; exit 1; }

if stage 1; then step "gate 1: dry run reports the right counts and deletes nothing"; check gate1; fi
if stage 2; then step "gate 2: enforce (policy through the API, order, trace states, quota counter, rollups, tombstones)"; check gate2; fi
if stage 3; then step "gate 3: SIGKILL while the job waits on a held row lock, rerun, same end state as an uninterrupted run"; check gate3; fi
if stage 4; then step "gate 4: SIGTERM, the mode file (kill switch) and the per-tick bounds"; check gate4; fi
if stage 5; then step "gate 5: privileges of the runtime and retention logins; no retention credential in the API's environment"; check gate5; fi
if stage 7; then
  step "gate 7: real browser: the Retention page and a run page in each trace state, against the real API"
  check ui-prepare
  export E2E_UI_PROJECT=main
  E2E_UI_RUN_AVAILABLE="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["available"])')"
  E2E_UI_RUN_EXPIRED="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["expired"])')"
  E2E_UI_RUN_DELETING="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["deleting"])')"
  E2E_UI_RUN_GONE="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["gone"])')"
  export E2E_UI_RUN_AVAILABLE E2E_UI_RUN_EXPIRED E2E_UI_RUN_DELETING E2E_UI_RUN_GONE
  web
  check ui-verify
fi
if stage 6; then step "gate 6: purge-project and purge-workspace"; check gate6; fi
if stage 8; then step "gate 8: cost: ingestion idle vs while enforcement runs in the background (informational timings)"; check gate8; fi
[ -z "${RETENTION_E2E_HOLD:-}" ] || { step "holding the stack for ${RETENTION_E2E_HOLD}s (development aid)"; sleep "$RETENTION_E2E_HOLD"; }
step "summary (logs of every process scanned)"
check summary || true
if [ "${#failed[@]}" -gt 0 ]; then printf 'RETENTION-E2E FAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
printf '\nRETENTION-E2E PASSED\n'
