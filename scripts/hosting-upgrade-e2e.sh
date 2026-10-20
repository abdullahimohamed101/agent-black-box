#!/usr/bin/env bash
# Upgrade E2E for hosting (Phase 23, step 8): scripts/hosting-upgrade.sh against a REAL compose stack (PostgreSQL 17, the migrate
# one-shot, api, worker, retention, web) on a throwaway "server checkout" that is a git repository with several releases.
#
# Proves: a first deploy; an upgrade that backs up (encrypted, off-site copy) before it changes anything, tags the previous images,
# migrates as the owner role and switches with --wait; an upgrade whose new API never gets healthy is ROLLED BACK (previous commit
# and previous image running again) because the schema revision did not change; an upgrade that also changed the schema revision is
# NOT rolled back and names the pre-upgrade bundle, which then drills OK; a PostgreSQL image change is refused with the checkout
# restored; a dirty tree, an unknown ref, a held lock, too little disk and a failing backup each stop it before anything changes.
#
# Throwaway: its own compose project (abb-hosting-up), network, volumes, no published ports, generated secrets under .local/ (0700).
# Images are tagged abb-hosting-up-*; the developer's agent-black-box-* tags are only READ (copied to our own tags) and never moved.
# shellcheck disable=SC2010,SC2012,SC2015,SC2016
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
APP="${HOSTING_UP_PROJECT:-abb-hosting-up}"; NET="$APP-net"
API_IMG=abb-hosting-up-api:local; WEB_IMG=abb-hosting-up-web:latest
RCLONE_IMAGE='rclone/rclone:1.69.3@sha256:1f497a86a6466395e62a5886613a14b7b18809543566ef9fa35fa1371a7ecc0f'
mkdir -p "$root/.local"; WORK="$(umask 077 && mktemp -d "$root/.local/hosting-up.XXXXXX")"
SRV="$WORK/abb"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HOSTING-UPGRADE-E2E FAILED: $*" >&2; exit 1; }
dc() { docker compose -p "$APP" --env-file "$WORK/app.env" -f "$SRV/docker-compose.yml" -f "$SRV/deploy/hosting/compose.production.yml" \
  -f "$SRV/deploy/hosting/compose.observability.yml" -f "$SRV/deploy/hosting/rig/compose.upgrade.yml" --project-directory "$SRV" \
  --profile app --profile ops --profile observability "$@"; }
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ] && [ -f "$WORK/app.env" ] && [ -d "$SRV" ]; then
    echo "--- last log lines ---" >&2
    for s in api migrate web; do dc logs --no-color --tail 8 "$s" 2>&1 | sed "s/^/[$s] /" >&2 || true; done
  fi
  if [ "${HOSTING_UP_KEEP:-0}" = 1 ]; then echo "left running: docker compose -p $APP down -v; docker network rm $NET; rm -rf $WORK" >&2; return; fi
  if [ -f "$WORK/app.env" ] && [ -d "$SRV" ]; then dc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker network rm "$NET" >/dev/null 2>&1 || true
  for t in $(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E "^abb-hosting-up-(api|web):" || true); do docker rmi "$t" >/dev/null 2>&1 || true; done
  rm -rf "$WORK" 2>/dev/null || { docker run --rm -v "$WORK:/w" --entrypoint sh "$RCLONE_IMAGE" -c 'rm -rf /w/* /w/.[!.]*' >/dev/null 2>&1; rm -rf "$WORK"; }
}
trap cleanup EXIT

step "preflight"
docker info >/dev/null 2>&1 || fail "docker is not reachable"
docker image inspect agent-black-box-api:local >/dev/null 2>&1 || fail "no agent-black-box-api:local image to copy (build it once: docker compose build api)"
docker image inspect agent-black-box-web:latest >/dev/null 2>&1 || fail "no agent-black-box-web:latest image to copy (docker compose --profile app build web)"
docker tag agent-black-box-api:local abb-hosting-up-api:base; docker tag agent-black-box-web:latest abb-hosting-up-web:base

step "a throwaway server checkout (git repository) with the hosting files of this worktree"
mkdir -p "$SRV"
tar -C "$root" -cf - docker-compose.yml .env.production.example infrastructure/docker deploy scripts/hosting-backup.sh scripts/hosting-restore-drill.sh \
  scripts/hosting-upgrade.sh scripts/hosting-compose.sh scripts/hosting-verify.sh scripts/hosting_backup_lib.py scripts/hosting_verify_lib.py scripts/hosting_server_checks.py \
  apps/api/Dockerfile apps/web/Dockerfile 2>/dev/null | tar -C "$SRV" -xf -
git -C "$SRV" init -q -b main; git -C "$SRV" config user.email rig@example.test; git -C "$SRV" config user.name rig
overlay() { # release number, extra lines for api, for migrate, for postgres (each a block already indented under its service)
  printf 'FROM abb-hosting-up-api:base\nLABEL abb.build=%s\n' "$1" >"$SRV/deploy/hosting/rig/Dockerfile.up-api"
  printf 'FROM abb-hosting-up-web:base\nLABEL abb.build=%s\n' "$1" >"$SRV/deploy/hosting/rig/Dockerfile.up-web"
  cat >"$SRV/deploy/hosting/rig/compose.upgrade.yml" <<YAML
services:
  worker: { image: $API_IMG }
  retention: { image: $API_IMG }
  backup: { image: $API_IMG }
  # A real compose build per release, made trivial: the base image plus a label, so every release has different image content.
  # The web image NAME comes from the production overlay (ABB_WEB_IMAGE in app.env below), exactly as on a server: the rig must
  # not name it here, or it would hide a production overlay that forgets to (P1-1: rollback never restored the web image).
  web:
    build: !override { context: ., dockerfile: deploy/hosting/rig/Dockerfile.up-web }
  migrate:
    image: $API_IMG
${3:-}
  api:
    image: $API_IMG
    labels: { abb.release: "$1" }
    build: !override { context: ., dockerfile: deploy/hosting/rig/Dockerfile.up-api }
${2:-}
${4:+  postgres:}
${4:-}
YAML
}
release() { # N api-extra migrate-extra postgres-extra : commit and tag vN
  # The commit advances HEAD, which on a real server stays on the running release until the upgrade checks the new one out:
  # so put HEAD back where it was.
  local prev=""; prev="$(git -C "$SRV" rev-parse -q --verify HEAD || true)"
  overlay "$1" "${2:-}" "${3:-}" "${4:-}"; git -C "$SRV" add -A >/dev/null; git -C "$SRV" commit -q -m "release $1"; git -C "$SRV" tag -f "v$1" >/dev/null
  if [ -n "$prev" ]; then git -C "$SRV" checkout -q --detach "$prev"; fi
}
head_release() { git -C "$SRV" describe --tags --exact-match HEAD 2>/dev/null || git -C "$SRV" rev-parse --short HEAD; }
running_build() { docker inspect "$(dc ps -q api)" --format '{{index .Config.Labels "abb.build"}}' 2>/dev/null; }
running_web_build() { docker inspect "$(dc ps -q web)" --format '{{index .Config.Labels "abb.build"}}' 2>/dev/null; }
worker_metrics_port() { docker inspect "$(dc ps -q worker)" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^ABB_WORKER_METRICS_PORT=//p'; }
image_id() { docker image inspect "$1" --format '{{.Id}}'; }

step "generated secrets and configuration (never printed)"
rand() { openssl rand -hex "$1"; }
cat >"$WORK/app.env" <<ENV
ABB_PUBLIC_ORIGIN=https://abb.example.test
ABB_POSTGRES_PASSWORD=$(rand 16)
ABB_RUNTIME_DB_PASSWORD=$(rand 16)
ABB_RETENTION_DB_PASSWORD=$(rand 16)
ABB_OIDC_ISSUER=https://idp.example.test
ABB_OIDC_CLIENT_ID=abb-up
ABB_OIDC_CLIENT_SECRET=$(rand 16)
ABB_EDGE_NETWORK=$NET
ABB_WEB_IMAGE=$WEB_IMG
ABB_OBS_UID=1000
ABB_OBS_GID=1000
ENV
printf '%s' "$(rand 24)" >"$WORK/backup.pass"; chmod 600 "$WORK/backup.pass"
mkdir -p "$WORK/bin" "$WORK/remote" "$WORK/textfile" "$WORK/backups"
printf '[offsite]\ntype = local\n' >"$WORK/rclone.conf"
cat >"$WORK/bin/rclone" <<WRAP
#!/usr/bin/env bash
exec docker run --rm -i --user "\$(id -u):\$(id -g)" -v "$WORK:$WORK" -e RCLONE_CONFIG="$WORK/rclone.conf" $RCLONE_IMAGE "\$@"
WRAP
chmod +x "$WORK/bin/rclone"
cat >"$WORK/backup.env" <<ENV
ABB_DIR=$SRV
ABB_BACKUP_DIR=$WORK/backups
ABB_BACKUP_PASSPHRASE_FILE=$WORK/backup.pass
ABB_OFFSITE_REMOTE=offsite:$WORK/remote/abb
ABB_TEXTFILE_DIR=$WORK/textfile
ABB_COMPOSE_PROJECT=$APP
ABB_COMPOSE_ENV_FILE=$WORK/app.env
# No ABB_COMPOSE_FILES: the scripts compute the list (hosting-compose.sh). ABB_OBS_UID in app.env switches the monitoring overlay on,
# as guide step 9 does, and only the rig overlay is added.
ABB_COMPOSE_EXTRA_FILES=deploy/hosting/rig/compose.upgrade.yml
ABB_OFFSITE_KEEP_MIN=3
ENV
chmod 600 "$WORK/backup.env"
export ABB_BACKUP_ENV="$WORK/backup.env" PATH="$WORK/bin:$PATH" RCLONE_CONFIG="$WORK/rclone.conf"
up() { (cd "$SRV" && scripts/hosting-upgrade.sh "$@"); }
variant() { local f="$WORK/variant.env"; cp "$WORK/backup.env" "$f"; printf '%s\n' "$@" >>"$f"; ABB_BACKUP_ENV="$f"; export ABB_BACKUP_ENV; }
base_env() { ABB_BACKUP_ENV="$WORK/backup.env"; export ABB_BACKUP_ENV; }
docker network create "$NET" >/dev/null

step "U1 first deploy of release 1 (no database yet: no backup, no rollback target)"
release 1
up v1 --no-verify 2>&1 | tee "$WORK/u1.out" | sed 's/^/  /'
grep -q 'first deploy' "$WORK/u1.out" && [ "$(running_build)" = 1 ] && [ "$(head_release)" = v1 ] || fail "first deploy did not land on release 1"
cli() { dc exec -T api python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Upgrade rig" --slug "up-$(date +%s)" >/dev/null
rev1="$(dc exec -T postgres psql -U abb -d abb -tAc 'select version_num from alembic_version' </dev/null | tr -d '[:space:]')"
echo "PASS U1 (schema revision $rev1)"

step "U2 upgrade to release 2: backup first, previous images tagged, migrate, switch with --wait"
old_id="$(image_id "$API_IMG")"
release 2
up v2 --no-verify 2>&1 | tee "$WORK/u2.out" | sed 's/^/  /'
[ "$(head_release)" = v2 ] && [ "$(running_build)" = 2 ] || fail "release 2 is not running"
bundle="$(sed -n 's/.*pre-upgrade bundle: \(abb-[0-9TZ]*\).*/\1/p' "$WORK/u2.out" | tail -1)"
[ -n "$bundle" ] && [ -f "$WORK/remote/abb/$bundle/COMPLETE" ] || fail "no pre-upgrade bundle with a COMPLETE marker at the remote"
[ "$(image_id "${API_IMG%%:*}:rollback")" = "$old_id" ] || fail "the previous image was not tagged :rollback"
# P1-3: the upgrade used the base files only and recreated the worker without the monitoring overlay's metrics port.
[ "$(worker_metrics_port)" = 9101 ] || fail "the upgrade removed the worker's metrics port (the monitoring overlay was dropped)"
echo "PASS U2 (bundle $bundle, rollback tag = release 1 image)"

step "U2b the stack is STOPPED (database volume intact): the upgrade still backs up first and never calls it a first deploy"
# Review P1-2: a stopped PostgreSQL used to read as "no database yet", which skipped the backup and disabled the rollback.
dc stop >/dev/null
up v2 --no-verify 2>&1 | tee "$WORK/u2b.out" | sed 's/^/  /'
if grep -q 'first deploy' "$WORK/u2b.out"; then fail "a stopped stack with a populated database was treated as a first deploy"; fi
grep -q 'step=backup' "$WORK/u2b.out" || fail "no backup was taken for the stopped stack"
b2b="$(sed -n 's/.*pre-upgrade bundle: \(abb-[0-9TZ]*\).*/\1/p' "$WORK/u2b.out" | tail -1)"
[ -n "$b2b" ] && [ -f "$WORK/remote/abb/$b2b/COMPLETE" ] || fail "the stopped-stack upgrade left no complete off-site bundle"
[ "$(running_build)" = 2 ] || fail "release 2 is not running after the stopped-stack upgrade"
echo "PASS U2b (bundle $b2b)"

step "U3 release 3 never gets healthy and the schema is unchanged: rolled back automatically"
prev_id="$(image_id "$API_IMG")"; prev_web_id="$(image_id "$WEB_IMG")"
release 3 '    healthcheck: { test: ["CMD", "false"], interval: 2s, timeout: 2s, retries: 2, start_period: 0s }'
if up v3 --no-verify >"$WORK/u3.out" 2>&1; then fail "a release that is never healthy was reported as a success"; fi
tail -4 "$WORK/u3.out" | sed 's/^/  /'
grep -q 'ROLLED BACK' "$WORK/u3.out" || fail "no rollback message"
[ "$(head_release)" != v3 ] && [ "$(git -C "$SRV" rev-parse HEAD)" = "$(git -C "$SRV" rev-parse v2)" ] || fail "the checkout was not restored to release 2"
[ "$(running_build)" = 2 ] || fail "release 2 is not the running build after the rollback (is $(running_build))"
[ "$(image_id "$API_IMG")" = "$prev_id" ] || fail "the previous image was not restored"
dc ps api --format '{{.Health}}' | grep -q healthy || fail "the API is not healthy after the rollback"
# P1-1: the dashboard rolls back with the API. Before, the web image was never tagged, so the new build kept running.
[ "$(running_web_build)" = 2 ] || fail "the web image was not rolled back (running build is $(running_web_build), expected 2)"
[ "$(image_id "$WEB_IMG")" = "$prev_web_id" ] || fail "the previous web image was not restored"
echo "PASS U3"

step "U4 release 4 changes the schema revision and then fails: NOT rolled back, the bundle is named, and it drills OK"
release 4 '    healthcheck: { test: ["CMD", "false"], interval: 2s, timeout: 2s, retries: 2, start_period: 0s }' \
  '    command: ["sh", "-c", "alembic upgrade head && psql \"postgresql$${ABB_DATABASE_URL#postgresql+asyncpg}\" -qc \"update alembic_version set version_num = '"'"'0105'"'"'\""]'
if up v4 --no-verify >"$WORK/u4.out" 2>&1; then fail "a failing release with a changed schema was reported as a success"; fi
tail -3 "$WORK/u4.out" | sed 's/^/  /'
grep -q 'NOT rolled back' "$WORK/u4.out" || fail "the script must refuse to roll the code back over a changed schema"
[ "$(head_release)" = v4 ] || fail "the checkout must stay on release 4 when the schema changed (is $(head_release))"
b4="$(sed -n 's/.*pre-upgrade bundle \(abb-[0-9TZ]*\).*/\1/p' "$WORK/u4.out" | head -1)"
[ -n "$b4" ] || fail "the failure message does not name the pre-upgrade bundle"
(cd "$SRV" && scripts/hosting-restore-drill.sh "$WORK/backups/$b4") | tail -1 | grep -q '^DRILL OK ' || fail "the named pre-upgrade bundle does not drill"
echo "PASS U4 (bundle $b4 drills OK)"

step "U5 a PostgreSQL image change is refused and the checkout is restored"
before="$(git -C "$SRV" rev-parse HEAD)"
release 5 "" "" '    image: postgres:18-alpine@sha256:0000000000000000000000000000000000000000000000000000000000000000'
if up v5 --no-verify >"$WORK/u5.out" 2>&1; then fail "a PostgreSQL image change was accepted"; fi
grep -q 'changes the PostgreSQL image' "$WORK/u5.out" || { cat "$WORK/u5.out"; fail "no refusal message"; }
[ "$(git -C "$SRV" rev-parse HEAD)" = "$before" ] || fail "the checkout was not restored"
echo "PASS U5"

step "U6 refusals before anything changes: dirty tree, unknown ref, held lock, too little disk, failing backup, dry run"
echo "# local edit" >>"$SRV/docker-compose.yml"
if up v2 --no-verify >"$WORK/r1.out" 2>&1; then fail "ran on a dirty tree"; fi
grep -q 'local modifications' "$WORK/r1.out" || fail "dirty tree message"; git -C "$SRV" checkout -q -- docker-compose.yml
if up v99 --no-verify >"$WORK/r2.out" 2>&1; then fail "ran with an unknown ref"; fi
grep -q "unknown ref" "$WORK/r2.out" || fail "unknown ref message"
mkdir "$SRV/.upgrade.lock"; if up v2 --no-verify >"$WORK/r3.out" 2>&1; then fail "ran with the lock held"; fi
grep -q 'another upgrade is running' "$WORK/r3.out" || fail "lock message"; rmdir "$SRV/.upgrade.lock"
if ABB_UPGRADE_MIN_FREE_KB=999999999999 up v2 --no-verify >"$WORK/r4.out" 2>&1; then fail "ran without enough disk"; fi
grep -q 'free on' "$WORK/r4.out" || fail "disk message"
variant ABB_OFFSITE_REMOTE=nowhere:abb
if up v2 --no-verify >"$WORK/r5.out" 2>&1; then fail "upgraded although the backup failed"; fi
grep -q 'pre-upgrade backup failed' "$WORK/r5.out" || { cat "$WORK/r5.out"; fail "backup failure message"; }
[ "$(git -C "$SRV" rev-parse HEAD)" = "$before" ] || fail "a failed backup still changed the checkout"
base_env
(cd "$SRV" && scripts/hosting-upgrade.sh v2 --dry-run) | tee "$WORK/r6.out" | sed 's/^/  /'; grep -q 'dry run' "$WORK/r6.out" || fail "dry run"
[ "$(git -C "$SRV" rev-parse HEAD)" = "$before" ] || fail "a dry run changed the checkout"
echo "PASS U6"

step "no secret in any output"
if grep -rE "$(cat "$WORK/backup.pass")" "$WORK"/*.out 2>/dev/null; then fail "the passphrase appeared in an output"; fi
echo "PASS output hygiene"
printf '\nHOSTING-UPGRADE-E2E PASSED\n'
