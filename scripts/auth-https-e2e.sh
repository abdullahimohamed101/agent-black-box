#!/usr/bin/env bash
# Auth E2E over https (Phase 19a, ADR-156, KI-075): the __Host-abb_login cookie in a real browser. `next start` has no
# https mode, so scripts/tls-proxy.mjs terminates TLS with a throwaway self-signed certificate (openssl, generated into a
# temporary directory) in front of the built web server. The API runs with an https ABB_WEB_ORIGIN and the DEVELOPMENT-ONLY
# fake provider redirects to it. Dedicated database abb_p19a_https, API :8167, fake provider :8168, web :3162 behind
# https://localhost:3161. Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${AUTH_HTTPS_E2E_DB_NAME:-abb_p19a_https}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
API_PORT=8167; OIDC_PORT=8168; WEB_PORT=3162; TLS_PORT=3161
API="http://localhost:$API_PORT"
export ABB_WEB_ORIGIN="https://localhost:$TLS_PORT"
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT"
export ABB_OIDC_CLIENT_ID="abb-dev"
export ABB_ENVIRONMENT=development
WORK="$(mktemp -d)"; pids=()
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "AUTH-HTTPS-E2E FAILED: $*" >&2; exit 1; }
for u in "$API/readyz" "http://localhost:$OIDC_PORT/.well-known/openid-configuration" "http://localhost:$WEB_PORT"; do
  if curl -sf "$u" >/dev/null 2>&1; then fail "something is already serving $u"; fi
done

step "self-signed certificate for localhost (throwaway, in a temporary directory)"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout "$WORK/key.pem" -out "$WORK/cert.pem" >/dev/null 2>&1 \
  || fail "openssl could not create the certificate"

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

step "start the fake provider (:$OIDC_PORT), the API (:$API_PORT), the web server (:$WEB_PORT) and TLS (:$TLS_PORT)"
"$root/scripts/fake-oidc.sh" "$OIDC_PORT" >"$WORK/oidc.log" 2>&1 & pids+=($!)
uv run uvicorn abb_api.main:app_from_env --factory --port "$API_PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
(cd "$root/apps/web" && ABB_API_INTERNAL_URL="$API" PORT="$WEB_PORT" pnpm exec next start --port "$WEB_PORT" >"$WORK/web.log" 2>&1) & pids+=($!)
node "$root/scripts/tls-proxy.mjs" "$TLS_PORT" "$WEB_PORT" "$WORK/key.pem" "$WORK/cert.pem" >"$WORK/tls.log" 2>&1 & pids+=($!)
for _ in $(seq 1 60); do
  curl -sf "$API/readyz" >/dev/null && curl -sf "http://localhost:$OIDC_PORT/.well-known/openid-configuration" >/dev/null \
    && curl -skf "https://localhost:$TLS_PORT/login" >/dev/null && break
  sleep 0.5
done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
curl -skf "https://localhost:$TLS_PORT/login" >/dev/null || fail "web over TLS did not start: $(tail -5 "$WORK/web.log" "$WORK/tls.log")"

step "provision a workspace and two invited people (generated, local-only)"
suffix="$(date +%s)$RANDOM"
WS="https-$suffix"; OWNER="owner-$suffix@auth.test"; ATTACKER="attacker-$suffix@auth.test"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Https Alpha" --slug "$WS" >/dev/null
cli add-member --workspace "$WS" --email "$OWNER" --role OWNER >/dev/null
cli add-member --workspace "$WS" --email "$ATTACKER" --role OWNER >/dev/null

step "Playwright over https (ignoreHTTPSErrors for the throwaway certificate)"
cd "$root/apps/web"
E2E_HTTPS_OWNER_EMAIL="$OWNER" E2E_HTTPS_ATTACKER_EMAIL="$ATTACKER" E2E_HTTPS_WORKSPACE="$WS" \
  pnpm exec playwright test e2e/auth-https.spec.ts
printf '\nAUTH-HTTPS-E2E PASSED\n'
