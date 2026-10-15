#!/usr/bin/env bash
# Hardening E2E (Phase 19a step 7, gates 1-7 of D6): a REAL stack on a dedicated database (abb_p19a_hardening): migrations, TWO API
# processes on different ports over the same database (and more, per gate, with their own settings), a worker, the DEVELOPMENT-ONLY
# fake OIDC provider on :8232, the built web server on :3220 and load generators (stdlib asyncio + httpx; no new dependency).
# The API and worker run as the least-privilege role (a login role created here, member of abb_runtime), exactly as production does.
#   1. limits hold across processes: ingestion events / bytes, policy writes, alert writes, sign-in starts through BOTH processes
#      (within the ADR-152 bound, not 2x); control with ABB_LIMITS_SHARED_ENABLED=0 (about 2x: the test can see the problem)
#   2. failed-auth flood bounded (ADR-153): 429 AUTH_THROTTLED + Retry-After, identical bodies, database load, valid traffic never
#      refused, X-Forwarded-For spoof resistance, one audit row per window, the real SDK with a throttled key
#   3. quotas (ADR-154): each at exactly its limit, existing data unaffected, replays, races through both processes (zero over), audit
#   4. edge (ADR-155): headers, /docs per environment, body cap sheds load with 503 and recovers, production refuses a privileged role
#   5. identity (ADR-156/157): sessions list / revoke, no users row without an invitation, inviter demotion revokes invitations
#   6. alert ports + search fairness (ADR-158)
#   7. cost: ingestion with everything on vs everything off, one and two processes (docs/benchmarks/phase-19a-hardening-e2e.md)
# Then Playwright drives a real browser against the web server (CSP smoke walk, quotas usage, sessions, the no-access notice).
# Needs Postgres on 5433 and `pnpm --filter @abb/web build`. Drops and recreates its own database on every run; never touches the shared
# dev/test databases. HARDENING_E2E_STAGES=1,2 limits the stages (development aid; CI runs them all); HARDENING_E2E_ROUNDS sets the
# benchmark rounds (default 7). HARDENING_E2E_LOG_DIR keeps the server logs (no payload bodies or secrets: INV-5) of a run.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; if [ -f ./.env ]; then . ./.env; else . ./.env.example; fi; set +a
DB_NAME="${HARDENING_E2E_DB_NAME:-abb_p19a_hardening}"
ROLE_NAME="abb_p19a_h_runtime"
OWNER_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
RUNTIME_PASSWORD="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 32 || true)"
API_A="${HARDENING_E2E_API_PORT:-8230}"; API_B=$((API_A + 1)); OIDC_PORT="${HARDENING_E2E_OIDC_PORT:-8232}"; WEB_PORT=3220
export ABB_DATABASE_URL="$OWNER_URL"                  # provisioning and migrations run as the owner; the serving processes get the runtime role
export ABB_WEB_ORIGIN="http://localhost:$WEB_PORT"     # fixed: the fake provider's redirect URI and the cookie origin
export ABB_ENVIRONMENT=development                     # http issuers, dev sessions exist only here and in test
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT" ABB_OIDC_CLIENT_ID="abb-dev"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_STREAM_REAUTH_SECONDS=2
export ABB_AUDIT_DENIALS_PER_MINUTE=1000               # the authorization probes would otherwise outrun the audit throttle
export ABB_ALERTS_DELIVERY_ENABLED=true ABB_ALERTS_SWEEP_INTERVAL_SECONDS=2 ABB_ALERTS_REQUEST_TIMEOUT_SECONDS=2
# An email destination needs a configured relay to be saved; nothing here sends mail (no rule fires an email).
export ABB_SMTP_HOST=127.0.0.1 ABB_SMTP_PORT=8249 ABB_SMTP_STARTTLS=false ABB_SMTP_FROM=alerts@abb.example.test
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  if [ -f "$WORK/spawned.pids" ]; then while read -r g; do [ -n "$g" ] && kill -TERM -- "-$g" 2>/dev/null || true; done <"$WORK/spawned.pids"; fi
  # The throwaway login role is cluster-level: drop it once its database is gone (best effort; the next run drops it again).
  (cd "$root/apps/api" && ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" ROLE_NAME="$ROLE_NAME" .venv/bin/python - <<'PY' >/dev/null 2>&1
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await conn.execute(f'drop database if exists "{os.environ["DB_NAME"]}" with (force)')
    await conn.execute(f'drop role if exists "{os.environ["ROLE_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
  ) || true
  # `oidc.log` is NOT kept: the development-only fake provider's log can hold authorization codes and id tokens.
  if [ -n "${HARDENING_E2E_LOG_DIR:-}" ]; then
    mkdir -p "$HARDENING_E2E_LOG_DIR" && for f in "$WORK"/*.log "$WORK"/results.jsonl; do [ "$(basename "$f")" = "oidc.log" ] || cp "$f" "$HARDENING_E2E_LOG_DIR"/ 2>/dev/null || true; done
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HARDENING-E2E FAILED: $*" >&2; exit 1; }
stage() { [ -z "${HARDENING_E2E_STAGES:-}" ] || [[ ",$HARDENING_E2E_STAGES," == *",$1,"* ]]; }
for p in $API_A $API_B $OIDC_PORT $WEB_PORT; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then fail "something is already listening on :$p"; fi
done
cd apps/api
PY=.venv/bin/python
[ -x "$PY" ] || fail "apps/api/.venv is missing (cd apps/api && uv sync --frozen)"

step "recreate the dedicated database $DB_NAME and the throwaway runtime login role, migrate to head (as the owner)"
ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$DB_NAME" ROLE_NAME="$ROLE_NAME" RUNTIME_PASSWORD="$RUNTIME_PASSWORD" $PY - <<'PY' || fail "database setup"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await conn.execute(f'drop database if exists "{os.environ["DB_NAME"]}" with (force)')
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

step "start the fake identity provider (:$OIDC_PORT), two API processes (:$API_A :$API_B, one database), a worker and the web server (:$WEB_PORT)"
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
for n in a b; do
  port=$API_A; [ "$n" = b ] && port=$API_B
  ABB_DATABASE_URL="$E2E_RUNTIME_DB_URL" $PY -m uvicorn abb_api.main:app_from_env --factory --host 127.0.0.1 --port "$port" >"$WORK/api-main-$n.log" 2>&1 & pids+=($!)
done
ABB_DATABASE_URL="$E2E_RUNTIME_DB_URL" $PY -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
(cd "$root/apps/web" && ABB_API_INTERNAL_URL="http://127.0.0.1:$API_A" PORT="$WEB_PORT" pnpm exec next start --port "$WEB_PORT" >"$WORK/web.log" 2>&1) & pids+=($!)
for _ in $(seq 1 80); do
  curl -sf "http://127.0.0.1:$API_A/readyz" >/dev/null && curl -sf "http://127.0.0.1:$API_B/readyz" >/dev/null \
    && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null && curl -sf "http://localhost:$WEB_PORT/login" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$API_A/readyz" >/dev/null || fail "API a did not become ready: $(tail -5 "$WORK/api-main-a.log")"
curl -sf "http://127.0.0.1:$API_B/readyz" >/dev/null || fail "API b did not become ready: $(tail -5 "$WORK/api-main-b.log")"
curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null || fail "fake provider did not start: $(tail -5 "$WORK/oidc.log")"
curl -sf "http://localhost:$WEB_PORT/login" >/dev/null || fail "web server did not start (pnpm --filter @abb/web build first): $(tail -5 "$WORK/web.log")"

step "provision the world (generated, local-only; secrets never printed): two workspaces, people for the roles, sessions"
suffix="$(date +%s)$RANDOM"
export E2E_WORKSPACE="hardening-$suffix" E2E_WORKSPACE2="hardening-other-$suffix" E2E_SUFFIX="$suffix"
export E2E_ROOT="$root" E2E_WORK="$WORK" E2E_STATE="$WORK/state.json" E2E_RESULTS="$WORK/results.jsonl" E2E_ORIGIN="$ABB_WEB_ORIGIN"
export E2E_OIDC_PORT="$OIDC_PORT" E2E_API_PORTS="$API_A,$API_B" E2E_WEB_PORT="$WEB_PORT" E2E_SPARE_PORT=$((API_A + 10))
: >"$WORK/state.json"; : >"$WORK/results.jsonl"; chmod 600 "$WORK/state.json"
failed=()
check() { $PY "$root/scripts/hardening_e2e_check.py" "$@" || failed+=("$*"); }
web() { (cd "$root/apps/web" && E2E_UI_PROJECT=ui-quotas E2E_UI_RUN="$UI_RUN" E2E_UI_STRANGER="$UI_STRANGER" E2E_OWNER_EMAIL="$E2E_OWNER_EMAIL" pnpm exec playwright test e2e/hardening-real.spec.ts "$@") || failed+=("web $*"); }
check provision
[ "${#failed[@]}" -eq 0 ] || { echo "HARDENING-E2E FAILED: provisioning ($failed)" >&2; exit 1; }
export E2E_OWNER_EMAIL="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["people"]["owner"])')"

if stage 1; then step "gate 1: limits hold across processes (events, bytes, policy writes, alert writes, sign-in starts) and a control with sharing off"; check gate1; fi
if stage 2; then step "gate 2: failed-auth flood bounded, valid traffic never refused, spoof resistance, audit once per window, the real SDK"; check gate2; fi
if stage 3; then step "gate 3: quotas at exactly the limit, races through both processes, audit, roles, isolation"; check gate3; fi
if stage 4; then step "gate 4: headers, docs per environment, body cap, production role check"; check gate4; fi
if stage 5; then step "gate 5: sessions, no row without an invitation, inviter demotion"; check gate5; fi
if stage 6; then step "gate 6: alert ports and search fairness"; check gate6; fi
if stage 8; then
  step "real browser: CSP smoke walk, quota usage, sessions, no-access notice"
  check ui-prepare
  UI_RUN="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["run_id"])')"
  UI_STRANGER="$($PY -c 'import json,os;print(json.load(open(os.environ["E2E_STATE"]))["ui"]["stranger"])')"
  web
  check ui-verify
fi
if stage 7; then step "gate 7: cost of everything on vs everything off (one and two processes)"; check gate7; fi
[ -z "${HARDENING_E2E_HOLD:-}" ] || { step "holding the stack for ${HARDENING_E2E_HOLD}s (development aid)"; sleep "$HARDENING_E2E_HOLD"; }
step "summary (logs of every process scanned)"
check summary || true
if [ "${#failed[@]}" -gt 0 ]; then printf 'HARDENING-E2E FAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
printf '\nHARDENING-E2E PASSED\n'
