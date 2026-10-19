#!/usr/bin/env bash
# Alerting E2E (Phase 17 step 4, gates 1-8 of D6): a dedicated database (abb_p17_alerts_e2e), the real API on :8210, a worker, the
# DEVELOPMENT-ONLY fake OIDC provider on :8211, the built web server on :3210 (started by Playwright) and local receivers started
# here (scripts/alerts_e2e_receivers.py): a signed webhook receiver that VERIFIES the HMAC and timestamp (:8213), a second one that
# can really go down (:8216), a Slack-shaped receiver (:8214), an SMTP sink (:8215) and a control port (:8212).
# Destinations must be HTTPS and the sender refuses loopback, so the API and the worker run with the TEST-ONLY network file
# (ABB_ALERTS_TEST_NETWORK_FILE: names resolve from a file, TLS trusts one generated self-signed certificate; refused outside
# development and test, unset by default). Everything else is the production code path.
#   1. each trigger fires exactly once end to end (webhook, Slack, email): approval requested, approval about to expire, a security
#      finding (the real SDK plants it), failed / timed-out / blocked runs, spend over a threshold
#   2. delivery is reliable: receiver down -> backoff -> delivered once (same alert id), dead letter at the cap, auto-pause, retry storm
#   3. outbound safety through the real stack: private/loopback/link-local/metadata/IPv6/HTTP refused at save; a name that rebinds to a
#      private address is refused at delivery; redirects are not followed
#   4. content-free: a planted sentinel never reaches a body, an email, a history row, an audit row, the alert tables or a log
#   5. secrets: none in the database in clear, masked everywhere, shown once, the HMAC verifies (and fails with a stale secret)
#   6. authorization and tenancy: roles, API keys, a second workspace, audit
#   7. noise control: dedupe, cooldown, rate limit, daily cap
#   8. system cost: ingestion latency with and without enabled rules (docs/benchmarks/phase-17-alerts-e2e.md)
# Then Playwright drives a real browser (OIDC sign-in as the owner): create a destination, the signing secret once, a rule with a
# preview line, send test, a history row after an alert fired, axe, console clean.
# Needs Postgres on 5433 and `pnpm --filter @abb/web build`. Drops and recreates its own database (abb_p17_alerts_e2e) on every run; never touches
# the shared dev/test databases.
# ALERTS_E2E_STAGES=a,b limits the stages (development aid; CI runs them all).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; if [ -f ./.env ]; then . ./.env; else . ./.env.example; fi; set +a
DB_NAME="${ALERTS_E2E_DB_NAME:-abb_p17_alerts_e2e}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
PORT="${ALERTS_E2E_API_PORT:-8210}"; API="http://localhost:$PORT"
OIDC_PORT="${ALERTS_E2E_OIDC_PORT:-8211}"
CTRL_PORT=8212; MAIN_PORT=8213; SLACK_PORT=8214; SMTP_PORT=8215; FLAKY_PORT=8216
export ABB_WEB_ORIGIN="http://localhost:3210"        # fixed: playwright.config.ts starts the web server there
export ABB_ENVIRONMENT=development                   # http issuers, dev sessions and the test network file exist only here and in test
export ABB_OIDC_ISSUER="http://localhost:$OIDC_PORT"
export ABB_OIDC_CLIENT_ID="abb-dev"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_FINDINGS_MIN_INTERVAL_SECONDS=0
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000      # the benchmark measures the pipeline, not the default throttle
export ABB_POLICY_DECIDE_PER_MINUTE_KEY=1000000 ABB_POLICY_DECIDE_PER_MINUTE_WORKSPACE=1000000 ABB_POLICY_WRITES_PER_MINUTE=1000
export ABB_AUDIT_DENIALS_PER_MINUTE=1000             # the authorization probes would otherwise outrun the audit throttle
# Alerting, tuned so waits are short and the limits are reachable: backoff 1 s doubling to 8 s, 4 attempts then a dead letter,
# "failing" after 3 consecutive failures and auto-pause after 6, 40 deliveries a minute and 60 a day per destination.
export ABB_ALERTS_DELIVERY_ENABLED=true
export ABB_ALERTS_BACKOFF_BASE_SECONDS=1 ABB_ALERTS_BACKOFF_MAX_SECONDS=8 ABB_ALERTS_MAX_ATTEMPTS=4
export ABB_ALERTS_FAILING_AFTER=3 ABB_ALERTS_PAUSE_AFTER_FAILURES=6
export ABB_ALERTS_RATE_PER_MINUTE=40 ABB_ALERTS_DAILY_CAP=60
export ABB_ALERTS_SWEEP_INTERVAL_SECONDS=2 ABB_ALERTS_REQUEST_TIMEOUT_SECONDS=2 ABB_ALERTS_SKIPPED_PER_WINDOW=1000
export ABB_ALERTS_WRITES_PER_MINUTE=100000 ABB_ALERTS_TESTS_PER_MINUTE=100000
export ABB_SMTP_HOST=127.0.0.1 ABB_SMTP_PORT=$SMTP_PORT ABB_SMTP_STARTTLS=false ABB_SMTP_FROM=alerts@abb.example.test
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
export ABB_ALERTS_TEST_NETWORK_FILE="$WORK/net.json"
# The receivers listen on these ports; the guard is the production one, the test adds them through the same setting an operator would (KI-162).
export ABB_ALERTS_ALLOWED_PORTS="443,$MAIN_PORT,$SLACK_PORT,$FLAKY_PORT"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  if [ -f "$WORK/spawned.pids" ]; then while read -r g; do [ -n "$g" ] && kill -TERM -- "-$g" 2>/dev/null || true; done <"$WORK/spawned.pids"; fi
  # ALERTS_E2E_LOG_DIR keeps the server logs of a failed run (they hold no payload bodies or secrets: INV-5) and the result lines.
  # `oidc.log` is NOT kept: the development-only fake provider's log can hold authorization codes and id tokens.
  if [ -n "${ALERTS_E2E_LOG_DIR:-}" ]; then
    mkdir -p "$ALERTS_E2E_LOG_DIR" && for f in "$WORK"/*.log "$WORK"/results.jsonl; do [ "$(basename "$f")" = "oidc.log" ] || cp "$f" "$ALERTS_E2E_LOG_DIR"/ 2>/dev/null || true; done
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "ALERTS-E2E FAILED: $*" >&2; exit 1; }
stage() { [ -z "${ALERTS_E2E_STAGES:-}" ] || [[ ",$ALERTS_E2E_STAGES," == *",$1,"* ]]; }
cd apps/api

step "recreate the dedicated database $DB_NAME, migrate to head"
ADMIN_URL="${ABB_DATABASE_URL%/*}/postgres" DB_NAME="$DB_NAME" uv run python - <<'PY'
import asyncio, os, asyncpg
async def main():
    url = os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    # A fresh database every run: rules left by an earlier run would keep alerting (their approvals expire later) and
    # their destinations would hit this run's receivers, which listen on the same ports.
    await conn.execute(f'drop database if exists "{os.environ["DB_NAME"]}" with (force)')
    await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
uv run alembic upgrade head >/dev/null 2>&1 || fail "migrations"

step "generate a self-signed certificate, start the receivers (:$MAIN_PORT :$FLAKY_PORT :$SLACK_PORT smtp :$SMTP_PORT control :$CTRL_PORT)"
for p in $PORT $OIDC_PORT $CTRL_PORT $MAIN_PORT $SLACK_PORT $SMTP_PORT $FLAKY_PORT; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then fail "something is already listening on :$p"; fi
done
NAMES="hooks.example.test slack.example.test rebind.example.test meta.example.test v6.example.test redirect.example.test"
uv run python "$root/scripts/alerts_e2e_receivers.py" gencert "$WORK" $NAMES
python3 - "$WORK" $NAMES <<'PY'
import json, sys
work, names = sys.argv[1], sys.argv[2:]
json.dump({"hosts": {n: ["93.184.216.34"] for n in names}, "ca_pem": f"{work}/cert.pem"}, open(f"{work}/net.json", "w"))
PY
uv run python "$root/scripts/alerts_e2e_receivers.py" serve "$WORK" $CTRL_PORT $MAIN_PORT $SLACK_PORT $FLAKY_PORT $SMTP_PORT >"$WORK/receivers.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do grep -q "receivers ready" "$WORK/receivers.log" 2>/dev/null && break; sleep 0.25; done
grep -q "receivers ready" "$WORK/receivers.log" || fail "receivers did not start: $(tail -5 "$WORK/receivers.log")"

step "start the fake identity provider (:$OIDC_PORT), the API (:$PORT) and a worker"
for u in "$API/readyz" "http://localhost:$OIDC_PORT/.well-known/openid-configuration"; do
  if curl -sf "$u" >/dev/null 2>&1; then fail "something is already serving $u; stop it or set ALERTS_E2E_API_PORT / _OIDC_PORT"; fi
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

step "provision the world (generated, local-only; secrets never printed): two workspaces, people for every role, projects"
suffix="$(date +%s)$RANDOM"
WS="alerts-e2e-$suffix"; WS2="alerts-other-$suffix"
OWNER="owner-$suffix@alerts.test"; ADMIN="admin-$suffix@alerts.test"; DEV="developer-$suffix@alerts.test"
VIEWER="viewer-$suffix@alerts.test"; SEC="security-$suffix@alerts.test"; BILLING="billing-$suffix@alerts.test"; ADMIN2="admin2-$suffix@other.test"
# Every provisioning command must succeed: a failure stops the run with the command's own stderr (never its
# stdout, which can hold a session token) instead of surfacing later as a confusing 401 or missing project.
cli() {
  local out
  out="$(uv run python -m abb_api.cli "$@" 2>"$WORK/cli.err")" || fail "cli $1 failed: $(tail -3 "$WORK/cli.err")"
  printf '%s' "$out"
}
cli create-workspace --name "Alerts E2E" --slug "$WS" >/dev/null
cli create-workspace --name "Alerts Other" --slug "$WS2" >/dev/null
PROJECTS="trig spend rel safety noise authz bench ui"
for p in $PROJECTS; do cli create-project --workspace "$WS" --name "$p" --slug "$p" --capture-mode full >/dev/null; done
cli create-project --workspace "$WS2" --name Foreign --slug foreign --capture-mode full >/dev/null
for pair in "$OWNER:OWNER" "$ADMIN:ADMIN" "$DEV:DEVELOPER" "$VIEWER:VIEWER" "$SEC:SECURITY" "$BILLING:BILLING"; do
  cli add-member --workspace "$WS" --email "${pair%%:*}" --role "${pair##*:}" >/dev/null
done
cli add-member --workspace "$WS2" --email "$ADMIN2" --role ADMIN >/dev/null
session() { ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$1" --hours 3; }
export E2E_OWNER_SESSION="$(session "$OWNER")" E2E_ADMIN_SESSION="$(session "$ADMIN")" E2E_DEVELOPER_SESSION="$(session "$DEV")"
export E2E_VIEWER_SESSION="$(session "$VIEWER")" E2E_SECURITY_SESSION="$(session "$SEC")" E2E_BILLING_SESSION="$(session "$BILLING")"
export E2E_OTHER_ADMIN_SESSION="$(session "$ADMIN2")"
for s in "$E2E_OWNER_SESSION" "$E2E_ADMIN_SESSION" "$E2E_DEVELOPER_SESSION" "$E2E_VIEWER_SESSION" "$E2E_SECURITY_SESSION" "$E2E_BILLING_SESSION" "$E2E_OTHER_ADMIN_SESSION"; do
  [ -n "$s" ] || fail "could not mint sessions"
done
rand() { LC_ALL=C tr -dc "$1" </dev/urandom | head -c "$2" || true; }
export E2E_PLANT="PLANTALERT${suffix}"               # the content sentinel: unique per run (the database is reused)
export E2E_AWS="AKIA$(rand 'A-Z0-9' 16)"; export E2E_PEM_BODY="MIIEowIBAAKCAQEA$(rand 'A-Za-z0-9' 40)"
export E2E_API="$API" E2E_WORKSPACE="$WS" E2E_WORKSPACE2="$WS2" E2E_PROJECTS="$PROJECTS"
export E2E_ORIGIN="$ABB_WEB_ORIGIN" E2E_STATE="$WORK/state.json" E2E_WORK="$WORK" E2E_ROOT="$root"
export E2E_RECV="http://127.0.0.1:$CTRL_PORT" E2E_MAIN_PORT=$MAIN_PORT E2E_SLACK_PORT=$SLACK_PORT E2E_FLAKY_PORT=$FLAKY_PORT E2E_NET_FILE="$ABB_ALERTS_TEST_NETWORK_FILE"
export E2E_OWNER_EMAIL="$OWNER"
export E2E_UI_TOKEN="ui$(rand 'a-f0-9' 24)"; export E2E_UI_SECRET_FILE="$WORK/ui-secret.txt"
export E2E_LOGS="$WORK/api.log:$WORK/worker.log:$WORK/receivers.log"
export E2E_ARTIFACT_DIR="$ABB_ARTIFACT_DIR"
: >"$WORK/state.json"; : >"$WORK/results.jsonl"; chmod 600 "$WORK/state.json"
export E2E_RESULTS="$WORK/results.jsonl"
failed=()
check() { uv run python "$root/scripts/alerts_e2e_check.py" "$@" || failed+=("$*"); }
web() { (cd "$root/apps/web" && E2E_ALERTS_API_URL="$API" pnpm exec playwright test e2e/alerts-real.spec.ts "$@") || failed+=("web $*"); }
mkdir -p "$root/docs/screenshots/phase-17"

check provision
if stage 1; then step "gate 1: every trigger fires exactly once (webhook, Slack, email); the real SDK plants the security finding"; check gate1-start; fi
if stage 7; then step "gate 7 (start): dedupe, cooldown, rate limit and the first half of the daily cap"; check gate7-start; fi
if stage 2; then step "gate 2: bounded retries, same alert id once the receiver is back, dead letter, auto-pause, retry storm"; check gate2; fi
if stage 3; then step "gate 3: outbound safety through the real stack"; check gate3; fi
if stage 6; then step "gate 6: authorization, API keys, tenancy, audit"; check gate6; fi
if stage 8; then step "gate 8: ingestion latency with and without enabled rules"; check gate8; fi
if stage 1; then check gate1-finish; fi
if stage 7; then step "gate 7 (finish): the daily cap, after the minute window has passed"; check gate7-finish; fi
if stage 9; then
  step "real browser: destination, signing secret once, rule with preview, send test, history row, axe, console"
  web --grep "@ui-setup"
  check ui-fire
  web --grep "@ui-history"
fi
if stage 4; then step "gate 4: content-free everywhere"; check gate4; fi
if stage 5; then step "gate 5: secrets (database scan, masking, shown once, HMAC)"; check gate5; fi
step "summary"
check summary || true
if [ "${#failed[@]}" -gt 0 ]; then printf 'ALERTS-E2E FAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
printf '\nALERTS-E2E PASSED\n'
