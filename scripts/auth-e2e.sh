#!/usr/bin/env bash
# Auth E2E (Phase 15): a real browser signs in through the OIDC flow against the DEVELOPMENT-ONLY fake provider
# (scripts/fake-oidc.sh), switches workspace, is refused or shown less as a VIEWER, accepts an invitation, sees a key token
# once, loses a live stream when removed, and signs out. A dedicated database (abb_p15, never the shared ones), the API on
# :8165 (ABB_STREAM_REAUTH_SECONDS=2 so a revoked stream ends within seconds), the fake provider on :8166, a worker, and the
# built web server on :3160 (started by Playwright). Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${AUTH_E2E_DB_NAME:-abb_p15}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${AUTH_E2E_API_PORT:-8165}"; API="http://localhost:$PORT"
OIDC_PORT="${AUTH_E2E_OIDC_PORT:-8166}"
export ABB_WEB_ORIGIN="http://localhost:3160"           # fixed: playwright.config.ts starts the web server there
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT"
export ABB_OIDC_CLIENT_ID="abb-dev"
export ABB_STREAM_REAUTH_SECONDS=2
export ABB_ENVIRONMENT=development                       # http issuers and dev sessions exist only here and in test
WORK="$(mktemp -d)"; pids=()
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "AUTH-E2E FAILED: $*" >&2; exit 1; }
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

step "start the fake identity provider (:$OIDC_PORT, development only), the API (:$PORT) and a worker"
for u in "$API/readyz" "http://localhost:$OIDC_PORT/.well-known/openid-configuration"; do
  if curl -sf "$u" >/dev/null 2>&1; then fail "something is already serving $u; stop it or set AUTH_E2E_API_PORT / AUTH_E2E_OIDC_PORT"; fi
done
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null || fail "fake provider did not start: $(tail -5 "$WORK/oidc.log")"

step "provision two workspaces, people (generated, local-only) and a write key (secrets never printed)"
suffix="$(date +%s)$RANDOM"
A="auth-a-$suffix"; B="auth-b-$suffix"
OWNER="owner-$suffix@auth.test"; INVITEE="invitee-$suffix@auth.test"; STREAMER="streamer-$suffix@auth.test"   # fresh people per run: the database is reused
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Auth Alpha" --slug "$A" >/dev/null
cli create-workspace --name "Auth Beta" --slug "$B" >/dev/null
cli create-project --workspace "$A" --name Demo --slug demo >/dev/null
cli add-member --workspace "$A" --email "$OWNER" --role OWNER >/dev/null
cli add-member --workspace "$B" --email "$OWNER" --role OWNER >/dev/null
cli add-member --workspace "$A" --email "$STREAMER" --role DEVELOPER >/dev/null
WRITE_KEY="$(cli create-key --workspace "$A" --project demo --scopes events:write runs:read --name auth-e2e-write)"
[[ "$WRITE_KEY" == abb_live_* ]] || fail "key creation"
STREAMER_SESSION="$(ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$STREAMER" --hours 1)"
[ -n "$STREAMER_SESSION" ] || fail "could not mint a session"

step "ingest a finished run whose first event carries a payload, and a run that stays in progress"
PAYLOAD_TEXT="AUTH-E2E-PAYLOAD-$RANDOM$RANDOM"
WRITE_KEY="$WRITE_KEY" API="$API" PAYLOAD_TEXT="$PAYLOAD_TEXT" uv run python - >"$WORK/runs.env" <<'PY'
import json, os, urllib.request
from datetime import datetime, timedelta, timezone
from abb_event_schema.ids import IdKind, new_id

def post(events):
    req = urllib.request.Request(os.environ["API"] + "/v1/events/batch", data=json.dumps({"events": events}).encode(),
        headers={"Authorization": "Bearer " + os.environ["WRITE_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        body = json.load(r)
    assert body["accepted"] == len(events) and body["rejected"] == 0, body

def build(name, finish):
    run, trace, root = new_id(IdKind.RUN), new_id(IdKind.TRACE), new_id(IdKind.SPAN)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=1)
    out, n = [], 0
    def ev(kind, attrs=None, **kw):
        nonlocal n, t0
        n += 1; t0 += timedelta(seconds=0.5)
        e = {"schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run, "trace_id": trace,
             "agent_id": "auth-agent", "event_type": kind, "sequence": n,
             "occurred_at": t0.isoformat().replace("+00:00", "Z"), "attributes": attrs or {}}
        e.update(kw); out.append(e)
    ev("run.started", {"run.name": name}, payload={"note": os.environ["PAYLOAD_TEXT"]})
    ev("agent.started", span_id=root)
    if finish:
        ev("agent.completed", span_id=root, status="success")
        ev("run.completed", status="success")
    post(out)
    return run

print("E2E_RUN_PAYLOAD=" + build("Auth: payload run", True))
print("E2E_RUN_LIVE=" + build("Auth: live run", False))
PY
set -a; . "$WORK/runs.env"; set +a
READ_KEY="$(cli create-key --workspace "$A" --scopes runs:read --name auth-e2e-script-read)"   # polling only; never given to the web
for id in "$E2E_RUN_PAYLOAD" "$E2E_RUN_LIVE"; do
  state=""
  for _ in $(seq 1 40); do
    state="$(curl -sS "$API/v1/runs/$id" -H "Authorization: Bearer $READ_KEY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("summary_state"))')"
    [[ "$state" == "current" ]] && break
    sleep 0.5
  done
  [[ "$state" == "current" ]] || fail "worker did not derive $id (state=$state)"
done

step "Playwright: real browser <-> web server <-> API <-> fake provider"
cd "$root/apps/web"
mkdir -p "$root/docs/screenshots/phase-15"
E2E_AUTH_API_URL="$API" E2E_WORKSPACE="$A" E2E_WORKSPACE_B="$B" E2E_STREAMER_SESSION="$STREAMER_SESSION" E2E_OWNER_EMAIL="$OWNER" E2E_INVITEE_EMAIL="$INVITEE" E2E_STREAMER_EMAIL="$STREAMER" \
  E2E_RUN_PAYLOAD="$E2E_RUN_PAYLOAD" E2E_RUN_LIVE="$E2E_RUN_LIVE" E2E_PAYLOAD_TEXT="$PAYLOAD_TEXT" \
  pnpm exec playwright test e2e/auth.spec.ts
printf '\nAUTH-E2E PASSED\n'
