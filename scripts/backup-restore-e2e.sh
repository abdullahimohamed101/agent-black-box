#!/usr/bin/env bash
# Backup and restore E2E, THE PERFORMED RESTORE DRILL (Phase 19b step 6, gate 6 of D6, ADR-162): a REAL stack on a dedicated database
# (abb_p19b6_drill_src), the API and the worker as a least-privilege login, a real artifact directory and a TLS webhook receiver
# (scripts/alerts_e2e_receivers.py, the alerts E2E's, reached through the same TEST-ONLY network file). The python driver
# (scripts/backup_restore_e2e.py) then:
#   seeds runs / events / artifacts through the real API, an alert destination with a sealed signing secret, a retention policy, quota
#   overrides and a session; takes a backup with the module CLI, an ENCRYPTED one, and (when Docker is available) one through the compose
#   `backup` service built from the API image; DESTROYS the source (drops the database, deletes the artifact directory);
#   proves a wrong passphrase, a tampered bundle and non-empty targets restore NOTHING; restores into a fresh database and directory;
#   starts the API and worker on it and checks the same runs, byte-identical artifacts, the alert secret still OPENS (the receiver
#   verifies the HMAC with the original secret), policy / quotas / sessions, alembic head, identical grants; restores an OLDER bundle
#   (upgraded on restore); rotates the alert key and checks the destination still delivers and the old keys can be retired.
# Durations, sizes and the machine are RECORDED (BACKUP_RESTORE_E2E_OUT); nothing is asserted about time.
# Needs Postgres on 5433 (and pg_dump / pg_restore / openssl on the PATH; Docker for the compose step). Never touches the shared dev/test
# databases. BACKUP_RESTORE_E2E_LOG_DIR keeps the process logs; BACKUP_RESTORE_E2E_RUNS sets the dataset (default 120 runs).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; if [ -f ./.env ]; then . ./.env; else . ./.env.example; fi; set +a
unset ABB_RETENTION_DATABASE_URL ABB_MIGRATION_DATABASE_URL ABB_RETENTION_MODE ABB_BACKUP_DATABASE_URL ABB_RESTORE_DATABASE_URL ABB_ROTATION_DATABASE_URL \
      ABB_RUNTIME_DB_PASSWORD ABB_RETENTION_DB_PASSWORD
# These scripts DROP and recreate their own databases and roles on the server ABB_DATABASE_URL names: refuse anything but a local one
# unless the operator says, in so many words, that it is meant (a shared or production server would lose its `abb_p19b6_*` objects).
case "$(printf '%s' "$ABB_DATABASE_URL" | sed -E 's#^[a-z+]+://([^@/]*@)?(\[[^]]*\]|[^:/?]*).*#\2#')" in
  localhost|127.0.0.1|'[::1]') ;;
  *) [ "${ABB_E2E_ALLOW_REMOTE_DATABASE:-}" = "1" ] || { echo "$(basename "$0"): ABB_DATABASE_URL is not a local database; set ABB_E2E_ALLOW_REMOTE_DATABASE=1 to run against it knowingly" >&2; exit 2; } ;;
esac
SRC_DB="abb_p19b6_drill_src"; RUNTIME_ROLE="abb_p19b6_drill_rt"
OWNER_URL="${ABB_DATABASE_URL%/*}/$SRC_DB"
rand() { LC_ALL=C tr -dc "$1" </dev/urandom | head -c "$2" || true; }
RUNTIME_PASSWORD="$(rand 'a-f0-9' 32)"
API_PORT="${BACKUP_RESTORE_E2E_API_PORT:-8250}"
CTRL_PORT=8252; MAIN_PORT=8253; SLACK_PORT=8254; SMTP_PORT=8255; FLAKY_PORT=8256
export ABB_WEB_ORIGIN="http://localhost:3000" ABB_ENVIRONMENT=development
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_FINDINGS_MIN_INTERVAL_SECONDS=0
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000 ABB_RATE_LIMIT_BURST_EVENTS=1000000
export ABB_ALERTS_DELIVERY_ENABLED=true ABB_ALERTS_SWEEP_INTERVAL_SECONDS=2 ABB_ALERTS_REQUEST_TIMEOUT_SECONDS=3
export ABB_ALERTS_WRITES_PER_MINUTE=100000 ABB_ALERTS_TESTS_PER_MINUTE=100000
export ABB_SMTP_HOST=127.0.0.1 ABB_SMTP_PORT=$SMTP_PORT ABB_SMTP_STARTTLS=false ABB_SMTP_FROM=alerts@abb.example.test
WORK="$(mktemp -d)"; pids=()
# The driver's Docker bind-mount directory is named after THIS run, so a cleanup never removes another drill's.
export BACKUP_DRILL_SHARE="$root/.local/backup-drill-$$"
export ABB_ALERTS_TEST_NETWORK_FILE="$WORK/net.json"
export ABB_ALERTS_ALLOWED_PORTS="443,$MAIN_PORT,$SLACK_PORT,$FLAKY_PORT"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  (cd "$root/apps/api" && ADMIN_URL="${OWNER_URL%/*}/postgres" ROLE_NAME="$RUNTIME_ROLE" .venv/bin/python - <<'PY' >/dev/null 2>&1
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    for row in await conn.fetch("select datname from pg_database where datname like 'abb\\_p19b6\\_drill\\_%'"):
        await conn.execute(f'drop database if exists "{row[0]}" with (force)')
    await conn.execute(f'drop role if exists "{os.environ["ROLE_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
  ) || true
  if [ -n "${BACKUP_RESTORE_E2E_LOG_DIR:-}" ]; then
    mkdir -p "$BACKUP_RESTORE_E2E_LOG_DIR" && for f in "$WORK"/*.log "$WORK"/results.jsonl; do cp "$f" "$BACKUP_RESTORE_E2E_LOG_DIR"/ 2>/dev/null || true; done
  fi
  rm -rf "$WORK" "$BACKUP_DRILL_SHARE" 2>/dev/null || true
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "BACKUP-RESTORE-E2E FAILED: $*" >&2; exit 1; }
for p in $API_PORT $CTRL_PORT $MAIN_PORT $SLACK_PORT $SMTP_PORT $FLAKY_PORT; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then fail "something is already listening on :$p"; fi
done
for tool in pg_dump pg_restore openssl; do command -v "$tool" >/dev/null || fail "$tool is not on the PATH"; done
cd apps/api
PY=.venv/bin/python
[ -x "$PY" ] || fail "apps/api/.venv is missing (cd apps/api && uv sync --frozen)"

step "recreate the dedicated database $SRC_DB and the runtime login, migrate to head (as the owner)"
ADMIN_URL="${OWNER_URL%/*}/postgres" DB_NAME="$SRC_DB" ROLE_NAME="$RUNTIME_ROLE" $PY - <<'PY' || fail "database setup"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    for row in await conn.fetch("select datname from pg_database where datname like 'abb\\_p19b6\\_drill\\_%'"):
        await conn.execute(f'drop database if exists "{row[0]}" with (force)')
    await conn.execute(f'drop role if exists "{os.environ["ROLE_NAME"]}"')
    await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
ABB_DATABASE_URL="$OWNER_URL" $PY -m alembic upgrade head >/dev/null 2>"$WORK/migrate.err" || fail "migrations: $(tail -3 "$WORK/migrate.err")"
ADMIN_URL="$OWNER_URL" ROLE_NAME="$RUNTIME_ROLE" RUNTIME_PASSWORD="$RUNTIME_PASSWORD" $PY - <<'PY' || fail "runtime role"
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    # A LOGIN role that inherits the migration-created abb_runtime privileges. It is a CLUSTER role: it survives the database being
    # dropped, so the restored database (whose ACLs name abb_runtime) serves the same login.
    await conn.execute(f'create role "{os.environ["ROLE_NAME"]}" login password \'{os.environ["RUNTIME_PASSWORD"]}\' in role abb_runtime')
    await conn.close()
asyncio.run(main())
PY

step "generate a self-signed certificate and start the TLS webhook receiver (:$MAIN_PORT, control :$CTRL_PORT)"
NAMES="hooks.example.test slack.example.test"
$PY "$root/scripts/alerts_e2e_receivers.py" gencert "$WORK" $NAMES
python3 - "$WORK" $NAMES <<'PY'
import json, sys
work, names = sys.argv[1], sys.argv[2:]
json.dump({"hosts": {n: ["93.184.216.34"] for n in names}, "ca_pem": f"{work}/cert.pem"}, open(f"{work}/net.json", "w"))
PY
$PY "$root/scripts/alerts_e2e_receivers.py" serve "$WORK" $CTRL_PORT $MAIN_PORT $SLACK_PORT $FLAKY_PORT $SMTP_PORT >"$WORK/receivers.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do grep -q "receivers ready" "$WORK/receivers.log" 2>/dev/null && break; sleep 0.25; done
grep -q "receivers ready" "$WORK/receivers.log" || fail "receivers did not start: $(tail -5 "$WORK/receivers.log")"

step "the drill (the API and worker are started and stopped by the driver, on the source and then on the restored database)"
suffix="$(date +%s)$RANDOM"
export E2E_NAME=BACKUP-RESTORE-E2E E2E_ROOT="$root" E2E_WORK="$WORK" E2E_STATE="$WORK/state.json" E2E_RESULTS="$WORK/results.jsonl" E2E_ORIGIN="$ABB_WEB_ORIGIN"
export E2E_WORKSPACE="drill-$suffix" E2E_WORKSPACE2="drill-unused-$suffix" E2E_SUFFIX="$suffix" E2E_ARTIFACT_DIR="$WORK/artifacts-src"
export E2E_API_PORT="$API_PORT" E2E_CTRL_PORT="$CTRL_PORT" E2E_MAIN_PORT="$MAIN_PORT" E2E_SERVER_URL="$OWNER_URL"
export E2E_OWNER_DB_URL="$OWNER_URL" E2E_RUNTIME_ROLE="$RUNTIME_ROLE" E2E_RUNTIME_PASSWORD="$RUNTIME_PASSWORD" E2E_OIDC_PORT=0
export E2E_RUNTIME_DB_URL="$OWNER_URL"      # replaced by the driver (use_database) before any process starts
: >"$WORK/state.json"; : >"$WORK/results.jsonl"; chmod 600 "$WORK/state.json"
failed=()
$PY "$root/scripts/backup_restore_e2e.py" drill || failed+=("drill")
step "summary (logs of every process scanned)"
$PY "$root/scripts/backup_restore_e2e.py" summary || failed+=("summary")
if [ "${#failed[@]}" -gt 0 ]; then printf 'BACKUP-RESTORE-E2E FAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
printf '\nBACKUP-RESTORE-E2E PASSED\n'
