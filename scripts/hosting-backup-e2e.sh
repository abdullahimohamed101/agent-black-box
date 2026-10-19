#!/usr/bin/env bash
# Backup and restore drill E2E for hosting (Phase 23, step 7, criterion A7). Runs scripts/hosting-backup.sh and
# scripts/hosting-restore-drill.sh against a real PostgreSQL + migrated schema + real API ingestion (events and an artifact), with a
# LOCAL fake "second provider": an rclone `local` remote (the pinned rclone image), so no cloud account is needed.
#
# Proves: an encrypted bundle (no plaintext dump), a verified copy at the remote with a COMPLETE marker, the metric file, a restore
# into a throwaway database with row counts equal to the manifest and the database/scratch directory gone afterwards, and the
# negative controls: wrong passphrase, tampered bundle, interrupted upload, a refused unsafe remote, a failed run that keeps the
# last success, the first-run check (KI-243) failing on a missing and a stale metric file, pruning that keeps the newest N, a held lock.
#
# NOT proven here (UNVERIFIED (env)): a real Backblaze B2 or Storage Box upload, systemd actually firing the timer.
# Throwaway: its own compose project, network, volumes, ports (18090, 18091) and generated secrets under .local/ (0700).
# A test driver: ls output is only ever read for names this script made, and A && B || fail is the intended shape here.
# shellcheck disable=SC2010,SC2012,SC2015
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
APP="${HOSTING_BK_PROJECT:-abb-hosting-bk}"; NET="$APP-net"; IMAGE=abb-hosting-bk-api:local
API_PORT="${HOSTING_BK_API_PORT:-18090}"; PING_PORT="${HOSTING_BK_PING_PORT:-18091}"
RCLONE_IMAGE='rclone/rclone:1.69.3@sha256:1f497a86a6466395e62a5886613a14b7b18809543566ef9fa35fa1371a7ecc0f'
UBUNTU_IMAGE='ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac'
mkdir -p "$root/.local"; WORK="$(umask 077 && mktemp -d "$root/.local/hosting-bk.XXXXXX")"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HOSTING-BACKUP-E2E FAILED: $*" >&2; exit 1; }
ping_pid=""; made_tag=0
dc() { docker compose -p "$APP" --env-file "$WORK/app.env" -f docker-compose.yml -f deploy/hosting/compose.production.yml \
  -f deploy/hosting/rig/compose.backup.yml --profile app --profile ops "$@"; }
cleanup() {
  local status=$?
  [ -z "$ping_pid" ] || kill "$ping_pid" 2>/dev/null || true
  if [ "$status" -ne 0 ] && [ -f "$WORK/app.env" ]; then
    echo "--- last log lines ---" >&2
    for s in api migrate postgres; do dc logs --no-color --tail 12 "$s" 2>&1 | sed "s/^/[$s] /" >&2 || true; done
  fi
  if [ "${HOSTING_BK_KEEP:-0}" = 1 ]; then echo "left running: docker compose -p $APP down -v; docker network rm $NET; rm -rf $WORK" >&2; return; fi
  if [ -f "$WORK/app.env" ]; then dc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker network rm "$NET" >/dev/null 2>&1 || true
  [ "$made_tag" = 1 ] && docker image rm "$IMAGE" >/dev/null 2>&1 || true
  # bundles written by the container user can be owned by uid 10001: remove them with the same image tag's user mapping
  rm -rf "$WORK" 2>/dev/null || { docker run --rm -v "$WORK:/w" --entrypoint sh "$RCLONE_IMAGE" -c 'rm -rf /w/* /w/.[!.]*' >/dev/null 2>&1; rm -rf "$WORK"; }
}
trap cleanup EXIT

step "preflight"
docker info >/dev/null 2>&1 || fail "docker is not reachable"
for port in "$API_PORT" "$PING_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then fail "port $port is already in use"; fi
done
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if docker image inspect agent-black-box-api:local >/dev/null 2>&1; then
    # Same image content, a tag of the rig's own (removed at the end; the image layers stay with the developer's tag).
    docker tag agent-black-box-api:local "$IMAGE"; made_tag=1
  else
    echo "building the API image (once)"; docker build -q -f apps/api/Dockerfile -t "$IMAGE" . >/dev/null; made_tag=1
  fi
fi

step "generated secrets and configuration (never printed)"
rand() { openssl rand -hex "$1"; }
cat >"$WORK/app.env" <<ENV
ABB_PUBLIC_ORIGIN=https://abb.example.test
ABB_POSTGRES_PASSWORD=$(rand 16)
ABB_RUNTIME_DB_PASSWORD=$(rand 16)
ABB_RETENTION_DB_PASSWORD=$(rand 16)
ABB_OIDC_ISSUER=https://idp.example.test
ABB_OIDC_CLIENT_ID=abb-bk
ABB_OIDC_CLIENT_SECRET=$(rand 16)
ABB_EDGE_NETWORK=$NET
ABB_BK_API_PORT=$API_PORT
ENV
printf '%s' "$(rand 24)" >"$WORK/backup.pass"; chmod 600 "$WORK/backup.pass"
printf '%s' "$(rand 24)" >"$WORK/other.pass"; chmod 600 "$WORK/other.pass"
mkdir -p "$WORK/bin" "$WORK/remote" "$WORK/textfile" "$WORK/backups"
printf '[offsite]\ntype = local\n' >"$WORK/rclone.conf"
# The fake second provider: the real rclone (pinned image) with a `local` backend. Mounted at the same path so paths match.
cat >"$WORK/bin/rclone" <<WRAP
#!/usr/bin/env bash
exec docker run --rm -i --user "\$(id -u):\$(id -g)" -v "$WORK:$WORK" -e RCLONE_CONFIG="$WORK/rclone.conf" $RCLONE_IMAGE "\$@"
WRAP
chmod +x "$WORK/bin/rclone"
REMOTE="offsite:$WORK/remote/abb"
# a dead-man receiver: records every ping it gets
python3 - "$PING_PORT" "$WORK/pings" <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        open(sys.argv[2], "a").write("ping\n"); self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
ping_pid=$!
cat >"$WORK/backup.env" <<ENV
ABB_DIR=$root
ABB_BACKUP_DIR=$WORK/backups
ABB_BACKUP_PASSPHRASE_FILE=$WORK/backup.pass
ABB_OFFSITE_REMOTE=$REMOTE
ABB_TEXTFILE_DIR=$WORK/textfile
ABB_COMPOSE_PROJECT=$APP
ABB_COMPOSE_ENV_FILE=$WORK/app.env
ABB_COMPOSE_FILES="docker-compose.yml deploy/hosting/compose.production.yml deploy/hosting/rig/compose.backup.yml"
ABB_LOCAL_KEEP_DAYS=7
ABB_LOCAL_KEEP_MIN=2
ABB_OFFSITE_KEEP_DAYS=30
ABB_OFFSITE_KEEP_MIN=3
ABB_DEADMAN_URL=http://127.0.0.1:$PING_PORT/ping
ENV
chmod 600 "$WORK/backup.env"
export ABB_BACKUP_ENV="$WORK/backup.env" PATH="$WORK/bin:$PATH" RCLONE_CONFIG="$WORK/rclone.conf"
backup() { scripts/hosting-backup.sh "$@"; }
drill() { scripts/hosting-restore-drill.sh "$@"; }
# The script sources its configuration file after reading the environment, so a variant is a copy of the file with a later line.
variant() { local f="$WORK/variant.env"; cp "$WORK/backup.env" "$f"; printf '%s\n' "$@" >>"$f"; ABB_BACKUP_ENV="$f"; export ABB_BACKUP_ENV; }
base_env() { ABB_BACKUP_ENV="$WORK/backup.env"; export ABB_BACKUP_ENV; }
psql_list() { dc exec -T postgres psql -U abb -d postgres -tAc "select datname from pg_database order by 1"; }

step "start postgres, migrate, the API; ingest events and upload an artifact through the real API"
docker network create "$NET" >/dev/null
dc up -d --wait --no-build postgres migrate api >"$WORK/up.log" 2>&1 || { tail -15 "$WORK/up.log" >&2; fail "stack did not start"; }
WS="bk-$(date +%s)"
cli() { dc exec -T api python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Backup rig" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Demo --slug demo >/dev/null
cli create-key --workspace "$WS" --project demo --scopes events:write runs:read artifacts:write --name bk-key >"$WORK/key"; chmod 600 "$WORK/key"
grep -q '^abb_live_' "$WORK/key" || fail "key creation"
seed="$(python3 scripts/hosting_e2e_seed.py "http://127.0.0.1:$API_PORT" "$WORK/key" --runs 5 --events 40)" || fail "seeding: $seed"
RUN_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_id"])' "$seed")"
python3 - "$API_PORT" "$WORK/key" "$RUN_ID" <<'PY' || fail "artifact upload"
import sys, urllib.request, secrets, time
port, keyfile, run = sys.argv[1:4]
A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
v = ((time.time_ns() // 1_000_000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
art = "art_" + "".join(A[(v >> (5 * i)) & 31] for i in range(25, -1, -1))
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/artifacts/{art}?run_id={run}&kind=other&name=note", data=b"a note kept in an artifact\n" * 50,
    method="PUT", headers={"Authorization": "Bearer " + open(keyfile).read().strip(), "Content-Type": "text/plain"})
assert urllib.request.urlopen(req, timeout=20).status in (200, 201)
PY
echo "seeded: 5 runs / $((5*41)) events, 1 artifact"

step "A7.1 first backup: encrypted bundle, verified, copied, checked, metric written, dead-man pinged"
backup >"$WORK/b1.out" 2>&1 || { cat "$WORK/b1.out" >&2; fail "the backup failed"; }
cat "$WORK/b1.out"
B1="$(ls -d "$WORK"/backups/abb-* | head -1)"; N1="$(basename "$B1")"
ls "$B1" | tr '\n' ' '; echo
[ -f "$B1/database.dump.enc" ] && [ ! -e "$B1/database.dump" ] || fail "the bundle is not encrypted"
if grep -q 'PGDMP' "$B1/database.dump.enc"; then fail "a plaintext dump header is visible in the encrypted file"; fi
[ -f "$WORK/remote/abb/$N1/COMPLETE" ] && [ -f "$WORK/remote/abb/$N1/database.dump.enc" ] || fail "the remote copy or its COMPLETE marker is missing"
cmp "$B1/database.dump.enc" "$WORK/remote/abb/$N1/database.dump.enc" || fail "remote copy differs"
[ "$(stat -f '%u' "$B1" 2>/dev/null || stat -c '%u' "$B1")" = "$(id -u)" ] || fail "the bundle is not owned by the deploy (current) user"
cat "$WORK/textfile/abb_backup.prom"
python3 scripts/hosting_backup_lib.py check --file "$WORK/textfile/abb_backup.prom" || fail "first-run check should pass after a backup"
[ "$(wc -l <"$WORK/pings" | tr -d ' ')" = 1 ] || fail "the dead-man URL was not pinged exactly once"
echo "PASS A7.1"

step "A7.2 restore drill from the REMOTE bundle into a throwaway database: DRILL OK, database and scratch gone"
drill "offsite:$WORK/remote/abb/$N1" | tee "$WORK/d1.out" || fail "the drill failed"
grep -q '^DRILL OK ' "$WORK/d1.out" || fail "no DRILL OK line"
rows="$(sed -n 's/^DRILL OK .* rows=\([0-9]*\) .*/\1/p' "$WORK/d1.out")"
[ "${rows:-0}" -gt 200 ] || fail "the drill compared only $rows rows: the data set is not realistic"
if psql_list | grep -q abb_restore_drill; then fail "the drill database still exists"; fi
if ls -d "$WORK"/backups/.drill.* >/dev/null 2>&1; then fail "a drill scratch directory is left"; fi
python3 scripts/hosting_backup_lib.py manifest-counts "$B1" | tr '\n' ' '; echo
echo "PASS A7.2 (psql -l lists: $(psql_list | tr '\n' ' '))"

step "A7.3 the drill can FAIL: wrong passphrase, tampered bundle, interrupted upload, existing drill database"
cp "$WORK/backup.pass" "$WORK/good.pass"; cp "$WORK/other.pass" "$WORK/backup.pass"
if drill "$B1" >"$WORK/n1.out" 2>&1; then fail "the drill passed with the wrong passphrase"; fi
grep -q 'DRILL FAILED: the bundle does not verify' "$WORK/n1.out" || { cat "$WORK/n1.out"; fail "wrong passphrase: unexpected message"; }
cp "$WORK/good.pass" "$WORK/backup.pass"
echo "  wrong passphrase: $(grep 'DRILL FAILED' "$WORK/n1.out")"
if psql_list | grep -q abb_restore_drill; then fail "a failed drill left its database"; fi
# tamper: flip one byte of the REMOTE copy's dump and drill from the remote
cp -R "$WORK/remote/abb/$N1" "$WORK/tamper-src"; python3 - "$WORK/remote/abb/$N1/database.dump.enc" <<'PY'
import sys
p = sys.argv[1]; b = bytearray(open(p, "rb").read()); b[len(b) // 2] ^= 0x01; open(p, "wb").write(bytes(b))
PY
if drill "offsite:$WORK/remote/abb/$N1" >"$WORK/n2.out" 2>&1; then fail "the drill passed with a tampered bundle"; fi
grep -q 'DRILL FAILED: the bundle does not verify' "$WORK/n2.out" || { cat "$WORK/n2.out"; fail "tamper: unexpected message"; }
echo "  tampered bundle: $(grep 'DRILL FAILED' "$WORK/n2.out")"
cp "$WORK/tamper-src/database.dump.enc" "$WORK/remote/abb/$N1/database.dump.enc"; rm -rf "$WORK/tamper-src"
# interrupted upload: no COMPLETE marker
mv "$WORK/remote/abb/$N1/COMPLETE" "$WORK/COMPLETE.keep"
if drill "offsite:$WORK/remote/abb/$N1" >"$WORK/n3.out" 2>&1; then fail "the drill accepted a bundle without COMPLETE"; fi
grep -q 'no COMPLETE marker' "$WORK/n3.out" || { cat "$WORK/n3.out"; fail "incomplete: unexpected message"; }
echo "  interrupted upload: $(grep 'DRILL FAILED' "$WORK/n3.out")"
mv "$WORK/COMPLETE.keep" "$WORK/remote/abb/$N1/COMPLETE"
# a leftover database of the same name is never touched
dc exec -T postgres psql -U abb -d postgres -qc 'CREATE DATABASE abb_restore_drill' >/dev/null
if drill "$B1" >"$WORK/n4.out" 2>&1; then fail "the drill ran over an existing database"; fi
grep -q 'already exists; refusing' "$WORK/n4.out" || { cat "$WORK/n4.out"; fail "existing db: unexpected message"; }
psql_list | grep -q abb_restore_drill || fail "the drill dropped a database it did not create"
dc exec -T postgres psql -U abb -d postgres -qc 'DROP DATABASE abb_restore_drill' >/dev/null
echo "  existing database: refused and left alone"
drill "offsite:$WORK/remote/abb/$N1" | grep -q '^DRILL OK ' || fail "the repaired bundle no longer drills"
echo "PASS A7.3"

step "A7.4 a failed run: remote unreachable keeps the last success, records the failure, keeps the verified local bundle"
before="$(awk '$1=="abb_hosting_backup_last_success_timestamp_seconds"{print $2}' "$WORK/textfile/abb_backup.prom")"
sleep 1
variant ABB_OFFSITE_REMOTE=nowhere:abb
if backup >"$WORK/f1.out" 2>&1; then fail "a backup to a missing remote succeeded"; fi
base_env
tail -2 "$WORK/f1.out"
after="$(awk '$1=="abb_hosting_backup_last_success_timestamp_seconds"{print $2}' "$WORK/textfile/abb_backup.prom")"
[ "$before" = "$after" ] || fail "a failed run overwrote the last success time"
grep -q '^abb_hosting_backup_last_run_success 0' "$WORK/textfile/abb_backup.prom" || fail "the failed run was not recorded"
n_local="$(ls -d "$WORK"/backups/abb-* | wc -l | tr -d ' ')"
[ "$n_local" = 2 ] || fail "expected the verified local bundle of the failed run to be kept (have $n_local)"
python3 scripts/hosting_backup_lib.py check --file "$WORK/textfile/abb_backup.prom" | tee "$WORK/c1.out" || true
grep -q '^FAIL backup.fresh  the most recent run failed' "$WORK/c1.out" || fail "the check did not flag the failed run"
echo "PASS A7.4"

step "A7.5 first-run check (KI-243) fails when nothing ever ran, and when the last success is stale"
variant ABB_TEXTFILE_DIR="$WORK/empty-textfile"
if backup --check >"$WORK/c2.out" 2>&1; then fail "the check passed with no metrics file"; fi
cat "$WORK/c2.out"; grep -q 'never completed' "$WORK/c2.out" || fail "message"
sed "s/^\(abb_hosting_backup_last_success_timestamp_seconds\) .*/\1 $(( $(date +%s) - 40*3600 ))/" "$WORK/textfile/abb_backup.prom" >"$WORK/stale.prom"
mkdir -p "$WORK/stale"; cp "$WORK/stale.prom" "$WORK/stale/abb_backup.prom"
variant ABB_TEXTFILE_DIR="$WORK/stale"
if backup --check >"$WORK/c3.out" 2>&1; then fail "the check passed with a 40 hour old backup"; fi
base_env
cat "$WORK/c3.out"; grep -q '40\.0 hours old' "$WORK/c3.out" || fail "stale message"
echo "PASS A7.5"

step "A7.6 pruning: old remote bundles go, the newest 3 complete bundles never do, partial bundles do not count"
mk_remote() { # name [complete]
  cp -R "$WORK/remote/abb/$N1" "$WORK/remote/abb/$1"; [ "${2:-yes}" = yes ] || rm -f "$WORK/remote/abb/$1/COMPLETE"; }
REF="$(date +%s)"   # one reference instant, so the same age always names the same bundle
old() { python3 -c "
from datetime import datetime, timedelta, timezone
import sys; sys.path.insert(0, 'scripts'); import hosting_backup_lib as l
print(l.bundle_name(datetime.fromtimestamp($REF, timezone.utc) - timedelta(days=$1)))"; }
for d in 31 35 40 90 120; do mk_remote "$(old $d)"; done
mk_remote "$(old 10)" no       # an interrupted upload from ten days ago
mk_remote "$(old 0.1)" no      # one that may still be running: must be left alone
mkdir -p "$WORK/remote/abb/notabundle"; echo keep >"$WORK/remote/abb/notabundle/file"
backup >"$WORK/b2.out" 2>&1 || { cat "$WORK/b2.out" >&2; fail "second backup"; }
grep -E 'prune|OK' "$WORK/b2.out"
ls "$WORK/remote/abb" | tr '\n' ' '; echo
left_old=0; for d in 31 35 40 90 120; do [ ! -e "$WORK/remote/abb/$(old $d)" ] || left_old=$((left_old+1)); done
# keep-days 30 and keep-min 3: bundles older than 30 days go EXCEPT those among the newest 3 complete (the two real ones + day-31)
echo "old bundles still present: $left_old (the day-31 bundle is the 3rd newest complete one and is protected)"
[ -d "$WORK/remote/abb/$(old 31)" ] || fail "the 3rd newest complete bundle was pruned"
for d in 35 40 90 120; do [ ! -e "$WORK/remote/abb/$(old $d)" ] || fail "day-$d bundle was not pruned"; done
[ ! -e "$WORK/remote/abb/$(old 10)" ] || fail "the stale partial bundle was not pruned"
[ -d "$WORK/remote/abb/$(old 0.1)" ] || fail "a fresh partial bundle was pruned"
[ -d "$WORK/remote/abb/notabundle" ] || fail "a foreign directory was pruned"
[ -d "$WORK/remote/abb/$N1" ] || fail "a real bundle was pruned"
n_local="$(ls -d "$WORK"/backups/abb-* | wc -l | tr -d ' ')"; echo "local bundles after prune (keep-min 2): $n_local"
echo "PASS A7.6"
step "A7.6b all bundles stale (a stalled schedule): the newest N still survive"
for d in 200 201 202 203; do mk_remote "$(old $d)"; done
rm -rf "$WORK/remote/abb/$N1"; for n in $(ls "$WORK/remote/abb" | grep -v notabundle); do
  case "$n" in "$(old 31)"|"$(old 200)"|"$(old 201)"|"$(old 202)"|"$(old 203)") ;; *) rm -rf "$WORK/remote/abb/$n" ;; esac; done
# remote now holds: day 31, 200..203 (complete). A run adds today's, so the newest 3 are today, 31, 200.
backup >"$WORK/b3.out" 2>&1 || { cat "$WORK/b3.out" >&2; fail "third backup"; }
ls "$WORK/remote/abb" | tr '\n' ' '; echo
[ "$(ls "$WORK/remote/abb" | grep -c '^abb-')" = 3 ] || fail "expected exactly the newest 3 bundles to remain"
[ -d "$WORK/remote/abb/$(old 31)" ] && [ -d "$WORK/remote/abb/$(old 200)" ] || fail "a protected bundle was deleted"
echo "PASS A7.6b"

step "A7.7 --latest finds the newest complete remote bundle and drills it"
drill --latest | grep -q '^DRILL OK ' || fail "--latest"
echo "PASS A7.7"

step "A7.7b orphans in backups/.staging: a partial bundle from a dead run (3 days old) is swept, a fresh one is left alone (review P2-4)"
export ABB_BACKUP_DIR="$WORK/backups"
staging_ls() { dc run --rm -T --no-deps --user 0:0 --entrypoint sh backup -c 'ls -A /backups/.staging' </dev/null; }
dc run --rm -T --no-deps --user 0:0 --entrypoint sh backup -c \
  'mkdir -p /backups/.staging/abb-20200101T000000Z/x /backups/.staging/abb-fresh && touch -d "3 days ago" /backups/.staging/abb-20200101T000000Z' </dev/null
backup >"$WORK/b-orphan.out" 2>&1 || { cat "$WORK/b-orphan.out" >&2; fail "the backup failed"; }
left="$(staging_ls | tr '\n' ' ')"
[ "$left" = "abb-fresh " ] || fail "staging holds [$left]; expected only the fresh entry"
dc run --rm -T --no-deps --user 0:0 --entrypoint sh backup -c 'rm -rf /backups/.staging/abb-fresh' </dev/null
echo "PASS A7.7b"

step "A7.8 a held lock stops a second run; units verify"
mkdir "$WORK/backups/.lock"
if backup >"$WORK/l.out" 2>&1; then fail "ran with the lock held"; fi
grep -q 'another backup is running' "$WORK/l.out" || fail "lock message"
rmdir "$WORK/backups/.lock"
mkdir -p "$WORK/units/opt/abb/scripts"
cp deploy/hosting/systemd/*.service deploy/hosting/systemd/*.timer "$WORK/units/"
cp scripts/hosting-backup.sh scripts/hosting-container-metrics.sh "$WORK/units/opt/abb/scripts/"
docker run --rm -v "$WORK/units:/u" "$UBUNTU_IMAGE" sh -c '
  apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq systemd >/dev/null 2>&1 || { echo "UNITS-NO-SYSTEMD"; exit 0; }
  mkdir -p /opt/abb/scripts /opt/abb/backups /var/lib/node_exporter/textfile && cp /u/opt/abb/scripts/*.sh /opt/abb/scripts/
  cp /u/*.service /u/*.timer /etc/systemd/system/
  echo "real-units-problem-lines=$(systemd-analyze verify /etc/systemd/system/abb-backup.service /etc/systemd/system/abb-backup.timer /etc/systemd/system/abb-container-metrics.service /etc/systemd/system/abb-container-metrics.timer 2>&1 | grep -c .)"
  # negative control: a misspelt directive and a missing ExecStart target must be reported
  sed "s/^NoNewPrivileges=yes/NoNewPriviliges=yes/; s#^ExecStart=.*#ExecStart=/opt/abb/scripts/missing.sh#" /u/abb-backup.service >/etc/systemd/system/planted.service
  echo "planted-units-problem-lines=$(systemd-analyze verify /etc/systemd/system/planted.service 2>&1 | grep -c .)"
  systemd-analyze calendar "*-*-* 02:30:00" | grep -i "next elapse" ' | tee "$WORK/units.out"
grep -q UNITS-NO-SYSTEMD "$WORK/units.out" && fail "could not install systemd in the container (no network?)"
grep -q '^real-units-problem-lines=0$' "$WORK/units.out" || fail "systemd-analyze reported a problem with the real units"
grep -qE '^planted-units-problem-lines=[1-9]' "$WORK/units.out" || fail "the unit check did not notice the planted mistakes"
echo "PASS A7.8"

step "A7.9 the runbook's 'Restoring onto a new server': wiped volumes, bundle from the off-site copy, restore into the empty abb, start the app"
# Review P1-4. The commands below are the ones in docs/runbooks/hosting-operations.md; keep the two in step.
NAME="$(rclone lsf -R --files-only "$REMOTE" | python3 scripts/hosting_backup_lib.py latest-complete)" || fail "no complete off-site bundle"
dc down -v --remove-orphans >/dev/null 2>&1 || fail "could not wipe the rig's volumes (the 'new server')"
export ABB_BACKUP_DIR="$WORK/backups"
mkdir -p "$ABB_BACKUP_DIR/incoming"
rclone copy "$REMOTE/$NAME" "$ABB_BACKUP_DIR/incoming/$NAME" --checksum >/dev/null || fail "could not download the bundle"
rm -f "$ABB_BACKUP_DIR/incoming/$NAME/COMPLETE"
dc up -d --wait --no-build postgres >"$WORK/new-pg.log" 2>&1 || { tail -5 "$WORK/new-pg.log" >&2; fail "postgres did not start on the new volume"; }
drill "$ABB_BACKUP_DIR/incoming/$NAME" | tee "$WORK/new-drill.out" | tail -1 | grep -q '^DRILL OK ' || fail "the drill on the new server"
ABB_BACKUP_PASSPHRASE="$(cat "$WORK/backup.pass")"; export ABB_BACKUP_PASSPHRASE
# shellcheck disable=SC2016
dc run --rm -T --user 0:0 -e ABB_BACKUP_PASSPHRASE --entrypoint sh backup -c \
  'ABB_RESTORE_DATABASE_URL="$ABB_BACKUP_DATABASE_URL" python -m abb_api.backup restore /backups/incoming/"$0" --artifact-dir /data/artifacts --passphrase-env ABB_BACKUP_PASSPHRASE && chown -R 10001:10001 /data/artifacts' \
  "$NAME" >"$WORK/new-restore.out" 2>&1 || { tail -8 "$WORK/new-restore.out" >&2; fail "the restore into the empty abb failed"; }
# What hosting-upgrade.sh does on this database: migrations as the owner (they set the role passwords), then the app.
dc up -d --wait --no-build migrate api >"$WORK/new-up.log" 2>&1 || { tail -10 "$WORK/new-up.log" >&2; fail "migrate/api did not start on the restored database"; }
bad=0; checked=0
while read -r table expected; do
  actual="$(dc exec -T postgres psql -U abb -d abb -tAc "SELECT count(*) FROM \"$table\"" </dev/null)"
  checked=$((checked + 1)); [ "$actual" = "$expected" ] || { echo "MISMATCH $table $expected $actual"; bad=$((bad + 1)); }
done < <(python3 scripts/hosting_backup_lib.py manifest-counts "$ABB_BACKUP_DIR/incoming/$NAME")
[ "$checked" -gt 0 ] && [ "$bad" = 0 ] || fail "restored row counts differ from the manifest ($bad of $checked tables)"
art="$(dc run --rm -T --no-deps --entrypoint sh backup -c 'find /data/artifacts -type f | wc -l; stat -c %u /data/artifacts' </dev/null | tr '\n' ' ')"
[ "$art" = "1 10001 " ] || fail "the artifact is missing or not owned by the API user (got: $art)"
curl -fsS "http://127.0.0.1:$API_PORT/readyz" >/dev/null || fail "/readyz on the restored server"
echo "PASS A7.9 ($checked tables equal the manifest, 1 artifact owned by uid 10001, API ready)"

step "no secret in any output"
if grep -rE "$(cat "$WORK/backup.pass")" "$WORK"/*.out "$WORK/up.log" 2>/dev/null; then fail "the passphrase appeared in an output"; fi
echo "PASS output hygiene"
printf '\nHOSTING-BACKUP-E2E PASSED\n'
