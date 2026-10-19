#!/usr/bin/env bash
# Hosting E2E (Phase 23, ADR-178): the real production overlay behind a real Caddy, locally. Caddy's own CA (local_certs, the
# `tls internal` of the plan) issues the certificate for abb.localhost; a fake https issuer (oidc.abb.localhost, behind the same
# Caddy) lets a person sign in under ABB_ENVIRONMENT=production; then scripts/hosting-verify.sh runs every assertion against
# https://abb.localhost:18443. Everything is throwaway: its own compose projects, network, volumes and generated secrets.
#
# What this does NOT prove (UNVERIFIED (env) until the owner-gated live run): Let's Encrypt issuance, DNS, ports 80/443 on a
# public IP, real Google sign-in, the host firewall, sshd hardening and the Hetzner/Backblaze accounts.
#
#   scripts/hosting-e2e.sh                 build, run, verify, tear everything down
#   HOSTING_E2E_KEEP_IMAGES=1 ...          keep the two built images between runs (faster iteration)
#   HOSTING_E2E_KEEP=1 ...                 leave the stack running for inspection (remove it with the printed commands)
# Needs Docker with Compose 2.24+, openssl and python3. Never reads .env; every secret is generated into a 0700 temp directory.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
APP="${HOSTING_E2E_PROJECT:-abb-hosting-rig}"; EDGE="$APP-edge"; NET="$APP-net"
HTTP_PORT="${HOSTING_E2E_HTTP_PORT:-18080}"; HTTPS_PORT="${HOSTING_E2E_HTTPS_PORT:-18443}"
SITE=abb.localhost; IDP=oidc.abb.localhost; ORIGIN="https://$SITE:$HTTPS_PORT"
IMAGE_API=abb-hosting-rig-api:local; IMAGE_WEB="$APP-web"
# Under the checkout, not /tmp: Colima shares the home directory with its VM, so a bind mount from /var/folders or /tmp would
# silently mount an empty directory (found by the first run of this rig). .local/ is git-ignored.
mkdir -p "$root/.local"; WORK="$(umask 077 && mktemp -d "$root/.local/hosting-e2e.XXXXXX")"; sampler=""
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HOSTING-E2E FAILED: $*" >&2; exit 1; }
NEGATIVE="${HOSTING_E2E_NEGATIVE:-0}"
extra=(); if [ "$NEGATIVE" = 1 ]; then extra=(-f deploy/hosting/rig/negative/compose.yml); fi
dc_app() { docker compose -p "$APP" --env-file "$WORK/app.env" -f docker-compose.yml -f deploy/hosting/compose.production.yml \
  -f deploy/hosting/rig/compose.rig.yml ${extra[@]+"${extra[@]}"} --profile app --profile ops "$@"; }
dc_edge() { docker compose -p "$EDGE" --env-file "$WORK/edge.env" -f deploy/hosting/edge/compose.yml \
  -f deploy/hosting/rig/edge.rig.yml "$@"; }
cleanup() {
  local status=$?
  if [ -n "$sampler" ]; then kill "$sampler" 2>/dev/null || true; fi
  if [ "$status" -ne 0 ] && [ -f "$WORK/app.env" ]; then
    echo "--- last log lines (structured logs never hold payloads or secrets) ---" >&2
    for s in api web worker migrate rig-oidc; do dc_app logs --no-color --tail 15 "$s" 2>&1 | sed "s/^/[$s] /" >&2 || true; done
    dc_edge logs --no-color --tail 15 caddy 2>&1 | sed 's/^/[caddy] /' >&2 || true
  fi
  if [ "${HOSTING_E2E_KEEP:-0}" = 1 ]; then
    echo "left running. Remove: docker compose -p $APP down -v; docker compose -p $EDGE down -v; docker network rm $NET; rm -rf $WORK" >&2
    return
  fi
  if [ -f "$WORK/app.env" ]; then dc_app down -v --remove-orphans >/dev/null 2>&1 || true; fi
  if [ -f "$WORK/edge.env" ]; then dc_edge down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker network rm "$NET" >/dev/null 2>&1 || true
  if [ "${HOSTING_E2E_KEEP_IMAGES:-0}" != 1 ]; then docker image rm "$IMAGE_API" "$IMAGE_WEB" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

step "preflight"
docker info >/dev/null 2>&1 || fail "docker is not reachable"
compose_version="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
python3 - "$compose_version" <<'PY' || fail "Docker Compose 2.24 or newer is required (found $compose_version): the overlay uses !reset and !override"
import sys
parts = [int(x) for x in sys.argv[1].split("-")[0].split(".")[:2]]
sys.exit(0 if parts >= [2, 24] else 1)
PY
command -v openssl >/dev/null || fail "openssl is required"
for port in "$HTTP_PORT" "$HTTPS_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then fail "port $port is already in use"; fi
done

step "generate throwaway secrets (never printed) and the rig configuration"
rand() { openssl rand -hex "$1"; }
metrics_token="$(rand 32)"
printf '%s' "$metrics_token" >"$WORK/metrics-token"
cat >"$WORK/app.env" <<ENV
ABB_PUBLIC_ORIGIN=$ORIGIN
ABB_POSTGRES_PASSWORD=$(rand 16)
ABB_RUNTIME_DB_PASSWORD=$(rand 16)
ABB_RETENTION_DB_PASSWORD=$(rand 16)
ABB_METRICS_TOKEN=$metrics_token
ABB_OIDC_ISSUER=https://$IDP:$HTTPS_PORT
ABB_OIDC_CLIENT_ID=abb-rig
ABB_OIDC_CLIENT_SECRET=$(rand 16)
ABB_EDGE_NETWORK=$NET
ABB_RIG_CA_BUNDLE=$WORK/ca-bundle.pem
ABB_RIG_CERTIFI_PATH=/placeholder
ENV
cat >"$WORK/edge.env" <<ENV
ABB_ACME_EMAIL=rig@example.invalid
ABB_SITE_ADDRESS=$SITE
ABB_EDGE_BIND=127.0.0.1
ABB_EDGE_HTTP_PORT=$HTTP_PORT
ABB_EDGE_HTTPS_PORT=$HTTPS_PORT
ABB_EDGE_NETWORK=$NET
ABB_EDGE_SITES_DIR=$WORK/sites
ABB_EDGE_GLOBALS_DIR=$root/deploy/hosting/rig/globals
ENV
mkdir -p "$WORK/sites"
if [ "$NEGATIVE" = 1 ]; then cp deploy/hosting/rig/negative/abb.caddy "$WORK/sites/abb.caddy"; else
  cp deploy/hosting/edge/abb.caddy.template "$WORK/sites/abb.caddy"; fi
cp deploy/hosting/rig/sites/oidc.caddy "$WORK/sites/oidc.caddy"
dc_app config -q || fail "the rig compose files do not render"

step "build the two images from this checkout (tags are the rig's own: a developer's agent-black-box-* images are untouched)"
dc_app build api web >"$WORK/build.log" 2>&1 || { tail -20 "$WORK/build.log" >&2; fail "image build failed"; }

step "start the shared Caddy (local CA) on 127.0.0.1:$HTTP_PORT / $HTTPS_PORT"
docker network create "$NET" >/dev/null
dc_edge up -d >/dev/null 2>&1 || fail "could not start Caddy"
for _ in $(seq 1 60); do
  dc_edge exec -T caddy cat /data/caddy/pki/authorities/local/root.crt >"$WORK/root.crt" 2>/dev/null && grep -q BEGIN "$WORK/root.crt" && break
  sleep 1
done
grep -q BEGIN "$WORK/root.crt" || fail "Caddy did not create its local CA"
certifi_path="$(dc_app run --rm --no-deps -T --entrypoint python migrate -c 'import certifi; print(certifi.where())' 2>/dev/null | tail -1)"
[ -n "$certifi_path" ] || fail "could not find the API image's certifi bundle"
{ dc_app run --rm --no-deps -T --entrypoint cat migrate "$certifi_path"; cat "$WORK/root.crt"; } >"$WORK/ca-bundle.pem" 2>/dev/null
sed -i.bak "s#^ABB_RIG_CERTIFI_PATH=.*#ABB_RIG_CERTIFI_PATH=$certifi_path#" "$WORK/app.env" && rm -f "$WORK/app.env.bak"

step "start the production-mode stack: postgres, migrate, api, worker, retention, web, and the fake https issuer"
dc_app up -d --wait --no-build postgres migrate api worker retention web rig-oidc >"$WORK/up.log" 2>&1 \
  || { tail -20 "$WORK/up.log" >&2; fail "the stack did not become healthy"; }

# Memory sampling for the limits (step 5 of the plan): the peak of each container while everything below runs.
( while true; do docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' 2>/dev/null | grep "^$APP" >>"$WORK/mem.log" || true; sleep 4; done ) &
sampler=$!

step "provision a workspace, a project, an ingest-and-read key and an invited person (generated, local-only)"
suffix="$(date +%s)$RANDOM"; WS="rig-$suffix"
cli() { dc_app exec -T api python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Hosting rig" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Demo --slug demo >/dev/null
cli create-key --workspace "$WS" --project demo --scopes events:write runs:read --name rig-key >"$WORK/key"
chmod 600 "$WORK/key"; grep -q '^abb_live_' "$WORK/key" || fail "key creation"
INVITED="invited-$suffix@rig.test"; STRANGER="stranger-$suffix@rig.test"
cli add-member --workspace "$WS" --email "$INVITED" --role OWNER >/dev/null

step "send traffic through the edge: three runs, the last left open for the event stream"
seed="$(python3 scripts/hosting_e2e_seed.py "$ORIGIN" "$WORK/key" --cacert "$WORK/root.crt" --resolve 127.0.0.1 --runs 3 --events 40 --leave-open)" \
  || fail "ingestion through the edge failed: $seed"
RUN_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_id"])' "$seed")"
echo "accepted: $(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["accepted"])' "$seed") events"

step "smoke load: 20 more runs (2,000 events) through the edge, then the dashboard pages rendered five times as the invited person"
python3 scripts/hosting_e2e_seed.py "$ORIGIN" "$WORK/key" --cacert "$WORK/root.crt" --resolve 127.0.0.1 --runs 20 --events 100 \
  --browse-as "$INVITED" --workspace "$WS" --rounds 5 >"$WORK/load.out" || fail "smoke load failed: $(cat "$WORK/load.out")"
echo "load: $(cat "$WORK/load.out")"

step "scripts/hosting-verify.sh against $ORIGIN"
verify=(scripts/hosting-verify.sh "$ORIGIN" --cacert "$WORK/root.crt" --resolve 127.0.0.1 --http-port "$HTTP_PORT"
  --metrics-token-file "$WORK/metrics-token" --stream-path "/v1/runs/$RUN_ID/stream" --stream-token-file "$WORK/key"
  --signin-email "$INVITED" --signin-stranger-email "$STRANGER" --strict)
if [ "$NEGATIVE" = 1 ]; then
  # A control, not a test of the product: the planted mistakes (trust proxy off, a naive proxy) must each turn an assertion red.
  "${verify[@]}" | tee "$WORK/verify.out" || true
  missing=""
  for check in xff.forged_header_cannot_evade_throttle flood.signin_start_limited paths.docs_readyz_closed paths.metrics_not_public \
      body.over_limit_refused_413 sse.not_compressed; do
    grep -q "^FAIL $check" "$WORK/verify.out" || missing="$missing $check"
  done
  [ -z "$missing" ] || fail "the verifier did not fail on the planted mistake(s):$missing"
  printf '\nHOSTING-E2E NEGATIVE CONTROL PASSED (every planted mistake turned its assertion red)\n'
  exit 0
fi
"${verify[@]}" | tee "$WORK/verify.out" || fail "hosting-verify.sh reported failures (see above)"

step "the rendered production configuration and the running containers"
scripts/hosting-verify.sh --config-only --env-file "$WORK/app.env" || fail "rendered-configuration check failed"
scripts/hosting-verify.sh --docker-project "$APP" || fail "published-port check failed"

step "rig-only checks that need the Docker network"
# KI-220/222: the scrape works inside the network (with the token), never at the edge (checked above).
# shellcheck disable=SC2016 # $T is expanded by the shell inside the container, on purpose
scrape="$(dc_app exec -T -e T="$metrics_token" web sh -c 'wget -q -O - --header "Authorization: Bearer $T" http://api:8000/metrics' 2>/dev/null | grep -c '^# HELP' || true)"
[ "$scrape" -gt 0 ] || fail "the in-network /metrics scrape returned no metrics"
echo "PASS scrape.in_network  /metrics with the token returned $scrape metric families over the private network"
# A database is unreachable from the edge network.
if dc_edge exec -T caddy sh -c 'nc -z -w 2 postgres 5432' >/dev/null 2>&1; then fail "postgres is reachable from the edge network"; fi
echo "PASS network.postgres_not_on_edge  the edge network cannot reach postgres:5432"
# The access log carries no cookie, authorization header, key or query string.
dc_edge exec -T caddy cat /var/log/caddy/abb-access.log >"$WORK/access.log" 2>/dev/null || true
[ -s "$WORK/access.log" ] || fail "the access log is empty"
if grep -qiE 'authorization|cookie|abb_live_|code=|state=|"request":\{[^}]*"headers"' "$WORK/access.log"; then fail "the access log holds a header, key or query string"; fi
echo "PASS log.access_log_clean  $(wc -l <"$WORK/access.log" | tr -d ' ') access-log lines, no header, cookie, key or query string"

step "memory seen by Docker during the run (peak MiB per container)"
python3 - "$WORK/mem.log" <<'PY'
import re, sys, collections
peak, limit = collections.defaultdict(float), {}
unit = {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}
for line in open(sys.argv[1], encoding="utf-8"):
    name, _, usage = line.strip().partition("|")
    m = re.match(r"([\d.]+)\s*(\w+)\s*/\s*([\d.]+)\s*(\w+)", usage)
    if not m:
        continue
    peak[name] = max(peak[name], float(m[1]) * unit[m[2]])
    limit[name] = float(m[3]) * unit[m[4]]
for name in sorted(peak):
    print(f"MEM {name:<40} peak {peak[name]:7.1f} MiB   limit {limit[name]:7.1f} MiB")
print(f"MEM total of peaks {sum(peak.values()):.1f} MiB")
PY
printf '\nHOSTING-E2E PASSED\n'
