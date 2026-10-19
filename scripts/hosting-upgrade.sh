#!/usr/bin/env bash
# Upgrade (or first deploy) of a hosted Agent BlackBox to a git tag or commit (Phase 23, ADR-178). Runs on the server as the deploy
# user from the checkout in /opt/abb.
#
#   scripts/hosting-upgrade.sh v1.4.0            backup, verify, check out, build, migrate, switch, verify
#   scripts/hosting-upgrade.sh main --no-build   use images that already exist (tagged <repo>:rollback / sha-...)
#   options: --skip-backup (not after the first deploy!)  --no-verify (skip scripts/hosting-verify.sh)  --dry-run (print the plan)
#
# The order is the safety: (1) preflight, (2) a verified, off-site-copied backup (abort here changes nothing), (3) previous images are
# tagged `rollback`, (4) check out + build, (5) migrations run as the OWNER role BEFORE any new API process starts (the same one-shot
# `migrate` service that applies the runtime and retention role passwords), (6) api, worker, retention, web start with `--wait`: Compose
# only returns once their health checks pass, (7) hosting-verify.sh runs against the public origin.
#
# Rollback. If (5) or (6) fails and the database schema revision is UNCHANGED, the previous commit and images are restored and started
# automatically. If the revision changed, the code is NOT rolled back (an older release may not run on a newer schema): the script
# says so and names the pre-upgrade bundle; fix forward, or restore it (docs/runbooks/backup-restore.md). A PostgreSQL image change in
# the new release is refused: a major version needs a dump and restore, never an in-place swap.
set -euo pipefail

CONF="${ABB_BACKUP_ENV:-/etc/abb/backup.env}"
# shellcheck disable=SC1090
if [ -r "$CONF" ]; then set -a; . "$CONF"; set +a; fi
ABB_DIR="${ABB_DIR:-/opt/abb}"
PROJECT="${ABB_COMPOSE_PROJECT:-}"
MIN_FREE_KB="${ABB_UPGRADE_MIN_FREE_KB:-5242880}"
KEEP_TAGS="${ABB_UPGRADE_KEEP_TAGS:-3}"
REF=""; BUILD=1; BACKUP=1; VERIFY=1; DRY=0
HERE="$(cd "$(dirname "$0")" && pwd)"

log() { printf 'hosting-upgrade: %s\n' "$*"; }
die() { printf 'hosting-upgrade: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --skip-backup) BACKUP=0 ;;
    --no-verify) VERIFY=0 ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$REF" ] || usage; REF="$1" ;;
  esac
  shift
done
[ -n "$REF" ] || usage
case "$REF" in *[!A-Za-z0-9._/-]*) die "unusual characters in the ref" 2 ;; esac

cd "$ABB_DIR"
export ABB_BACKUP_DIR="${ABB_BACKUP_DIR:-$ABB_DIR/backups}"
compose_args=()
[ -n "$PROJECT" ] && compose_args+=(-p "$PROJECT")
[ -n "${ABB_COMPOSE_ENV_FILE:-}" ] && compose_args+=(--env-file "$ABB_COMPOSE_ENV_FILE")
# shellcheck source=hosting-compose.sh
. "$HERE/hosting-compose.sh"; abb_compose_resolve
for f in $compose_files; do compose_args+=(-f "$f"); done
dc() { docker compose "${compose_args[@]}" --profile app "${compose_profiles[@]}" "$@"; }

# ---- 1. preflight ---------------------------------------------------------------------------------------------------------------
for tool in git docker python3; do command -v "$tool" >/dev/null || die "$tool is required" 2; done
git rev-parse --git-dir >/dev/null 2>&1 || die "$ABB_DIR is not a git checkout" 2
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "the checkout has local modifications; commit or discard them first (nothing was changed)" 2
python3 - "$(docker compose version --short 2>/dev/null | sed 's/^v//')" <<'PY' || die "Docker Compose 2.24 or newer is required" 2
import sys
sys.exit(0 if [int(x) for x in sys.argv[1].split("-")[0].split(".")[:2]] >= [2, 24] else 1)
PY
if [ -z "${ABB_COMPOSE_ENV_FILE:-}" ]; then
  [ -f .env ] || die "no .env: run scripts/hosting-secrets.sh first" 2
  mode="$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env)"
  [ "$mode" = 600 ] || die ".env must be mode 0600 (is $mode)" 2
fi
free_kb="$(df -Pk "$ABB_DIR" | awk 'NR==2{print $4}')"
[ "${free_kb:-0}" -ge "$MIN_FREE_KB" ] || die "less than $((MIN_FREE_KB / 1024)) MiB free on $ABB_DIR; free space first (docs/runbooks/hosting-operations.md)" 2
LOCK="$ABB_DIR/.upgrade.lock"
mkdir "$LOCK" 2>/dev/null || die "another upgrade is running (or died: remove $LOCK)" 75
cleanup() { rmdir "$LOCK" 2>/dev/null || true; }
trap cleanup EXIT

if [ -n "$(git remote)" ]; then git fetch --tags --prune --quiet || die "git fetch failed (nothing was changed)" 2; fi
NEW_SHA="$(git rev-parse --verify --quiet "$REF^{commit}")" || die "unknown ref '$REF' (nothing was changed)" 2
PREV_SHA="$(git rev-parse HEAD)"

# The schema revision. Prints nothing when `alembic_version` does not exist (a database that was never migrated); returns 1 when the
# query itself fails (PostgreSQL stopped or unreachable). A failed query is never "no database": that mistake skipped the backup
# and the rollback for a populated database whose container happened to be stopped (review P1-2).
revision() {
  local has
  has="$(dc exec -T postgres psql -U abb -d abb -v ON_ERROR_STOP=1 -tAc "select to_regclass('public.alembic_version') is not null" </dev/null 2>/dev/null)" || return 1
  [ "$(printf '%s' "$has" | tr -d '[:space:]')" = t ] || return 0
  dc exec -T postgres psql -U abb -d abb -v ON_ERROR_STOP=1 -tAc "select version_num from alembic_version" </dev/null 2>/dev/null | tr -d '[:space:]'
}
# A cheap `dc config` first: an incomplete .env (a secret still empty) is reported by Compose with the variable's name instead of a
# traceback from the JSON readers below (review P3-3).
rendered() { dc config --format json; }
built_images() { rendered | python3 -c 'import json,sys; c=json.load(sys.stdin)["services"]; print("\n".join(sorted({s["image"] for s in c.values() if "build" in s and s.get("image")})))'; }
pg_image() { rendered | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["postgres"]["image"])'; }
pg_volume() { rendered | python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["pgdata"]["name"])'; }

if ! cfg_err="$(dc config -q 2>&1 >/dev/null)"; then
  printf '%s\n' "$cfg_err" | sed 's/^/  /' >&2
  die "docker compose cannot render the configuration (above): complete .env first (nothing was changed)" 2
fi
# "First deploy" means there is no data volume, or its database was never migrated. It is never decided from a failed query.
FIRST=0; PREV_REV=""
if docker volume inspect "$(pg_volume)" >/dev/null 2>&1; then
  if [ "$DRY" = 1 ] && [ -z "$(dc ps -q --status running postgres 2>/dev/null)" ]; then
    PREV_REV="unknown (PostgreSQL is stopped; a real run starts it first)"
  else
    dc up -d --no-build --wait postgres >/dev/null 2>&1 || die "the PostgreSQL data volume exists but PostgreSQL would not start; refusing to treat this as a first deploy. Read: docker compose logs postgres (nothing was changed)." 2
    PREV_REV="$(revision)" || die "could not read the schema revision from a running PostgreSQL; refusing to guess (nothing was changed)." 2
    [ -n "$PREV_REV" ] || FIRST=1
  fi
else
  FIRST=1
fi
PREV_PG="$(pg_image)"
IMAGES="$(built_images)"
log "from ${PREV_SHA:0:12} to ${NEW_SHA:0:12} ($REF); schema revision now: ${PREV_REV:-none (first deploy)}"
if [ "$DRY" = 1 ]; then
  plan="tag the previous images rollback, check out $REF, migrate as the owner role, start api worker retention web with --wait"
  if [ "$BUILD" = 1 ]; then plan="build the images, $plan"; fi
  if [ "$BACKUP" = 1 ] && [ "$FIRST" = 0 ]; then plan="back up and verify, $plan"; fi
  if [ "$VERIFY" = 1 ]; then plan="$plan, run hosting-verify.sh"; fi
  log "dry run: would $plan"
  git log --oneline "$PREV_SHA..$NEW_SHA" | head -20 || true
  exit 0
fi

# ---- 2. backup ------------------------------------------------------------------------------------------------------------------
BUNDLE="(none)"
if [ "$FIRST" = 1 ]; then
  log "first deploy: no database yet, no backup to take"
elif [ "$BACKUP" = 1 ]; then
  log "step=backup (encrypted, verified, copied off-site; abort here changes nothing)"
  out="$("$HERE/hosting-backup.sh")" || die "the pre-upgrade backup failed: nothing was changed. Fix the backup first." 3
  printf '%s\n' "$out" | sed 's/^/  /'
  BUNDLE="$(printf '%s\n' "$out" | sed -n 's/.*OK bundle=\([^ ]*\).*/\1/p' | tail -1)"
  [ -n "$BUNDLE" ] || die "the backup finished without naming its bundle" 3
else
  log "WARNING: --skip-backup: there is no restore point for this upgrade"
fi

# ---- 3. mark the images that run now so a rollback can restore them ---------------------------------------------------------------
for img in $IMAGES; do
  if docker image inspect "$img" >/dev/null 2>&1; then docker tag "$img" "${img%%:*}:rollback"; fi
done

rollback_or_report() {
  local why="$1" cur; cur="$(revision || true)"
  log "FAILED: $why"
  if [ "$FIRST" = 1 ]; then
    die "first deploy failed; nothing to roll back to. Read: docker compose logs, then fix and run this again."
  fi
  if [ "$cur" = "$PREV_REV" ]; then
    log "schema revision unchanged ($cur): rolling the code back to ${PREV_SHA:0:12}"
    git checkout --detach --quiet "$PREV_SHA"
    for img in $IMAGES; do docker image inspect "${img%%:*}:rollback" >/dev/null 2>&1 && docker tag "${img%%:*}:rollback" "$img"; done
    if dc up -d --no-build --wait api worker retention web >"$ABB_DIR/.upgrade-rollback.log" 2>&1; then
      rm -f "$ABB_DIR/.upgrade-rollback.log"
      die "ROLLED BACK to ${PREV_SHA:0:12}; the previous release is running again. The upgrade did not happen." 1
    fi
    tail -5 "$ABB_DIR/.upgrade-rollback.log" | sed 's/^/  /' >&2
    die "ROLLBACK FAILED: the previous release did not start either. The pre-upgrade bundle is $BUNDLE; docs/runbooks/backup-restore.md." 1
  fi
  die "NOT rolled back: the schema revision is now ${cur:-unknown} (was $PREV_REV) and an older release may not run on it. Fix forward, or restore the pre-upgrade bundle $BUNDLE (docs/runbooks/backup-restore.md)." 1
}

# ---- 4. check out, refuse a Postgres image change, build ---------------------------------------------------------------------------
log "step=checkout $REF"
git checkout --detach --quiet "$NEW_SHA"
NEW_PG="$(pg_image)"
if [ "$NEW_PG" != "$PREV_PG" ] && [ "$FIRST" = 0 ]; then
  git checkout --detach --quiet "$PREV_SHA"
  die "the new release changes the PostgreSQL image ($PREV_PG -> $NEW_PG). A major version cannot be swapped in place: dump and restore (docs/runbooks/migrations.md). The checkout was restored." 4
fi
git log --oneline "$PREV_SHA..$NEW_SHA" | head -20 | sed 's/^/  /' || true
IMAGES="$(built_images)"
if [ "$BUILD" = 1 ]; then
  log "step=build"
  dc build api web || rollback_or_report "the image build failed"
fi
for img in $IMAGES; do
  if docker image inspect "$img" >/dev/null 2>&1; then docker tag "$img" "${img%%:*}:sha-${NEW_SHA:0:12}"; fi
done

# ---- 5. migrations as the owner role, before any new API process ----------------------------------------------------------------------
log "step=migrate (owner role; applies the runtime and retention role passwords)"
dc up -d --no-build --wait postgres >/dev/null || rollback_or_report "PostgreSQL did not become healthy"
dc run --rm --no-deps -T migrate >/dev/null || rollback_or_report "the migration failed"

# ---- 6. the health-gated switch ---------------------------------------------------------------------------------------------------------
log "step=switch (api, worker, retention, web; Compose waits for their health checks)"
dc up -d --no-build --wait api worker retention web >/dev/null || rollback_or_report "a service did not become healthy"

# ---- 7. verify the public origin -------------------------------------------------------------------------------------------------------
if [ "$VERIFY" = 1 ]; then
  origin="$(grep -E '^ABB_PUBLIC_ORIGIN=' "${ABB_COMPOSE_ENV_FILE:-.env}" | head -1 | cut -d= -f2-)"
  log "step=verify $origin"
  token_args=(); [ -r secrets/metrics_token ] && token_args=(--metrics-token-file secrets/metrics_token)
  if ! "$HERE/hosting-verify.sh" "$origin" "${token_args[@]}" --no-flood; then
    die "the release is running but scripts/hosting-verify.sh found problems (above). It was NOT rolled back; decide: fix forward or restore the bundle $BUNDLE." 5
  fi
fi

# ---- 8. housekeeping that is safe: only this project's own sha tags beyond the newest N --------------------------------------------------
for img in $IMAGES; do
  repo="${img%%:*}"
  docker image ls "$repo" --format '{{.Tag}}' 2>/dev/null | grep -E '^sha-[0-9a-f]{12}$' | tail -n +"$((KEEP_TAGS + 1))" | while read -r tag; do
    docker rmi "$repo:$tag" >/dev/null 2>&1 || true
  done
done
log "OK now ${NEW_SHA:0:12} ($REF); schema revision $(revision || echo unknown); pre-upgrade bundle: $BUNDLE"
log "disk: $(docker system df --format '{{.Type}} {{.Size}} (reclaimable {{.Reclaimable}})' 2>/dev/null | tr '\n' ';')"
log "later, when sure: docker image prune (dangling images only). NEVER docker volume prune: it deletes the database."
