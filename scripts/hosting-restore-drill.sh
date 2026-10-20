#!/usr/bin/env bash
# Restore drill (Phase 23, ADR-178, D6): proves a backup can be restored. Takes a bundle (a local directory, or an rclone path
# remote:path/abb-<timestamp>), verifies it, restores it into a THROWAWAY database and artifact directory with the existing tool,
# compares the live row counts with the manifest, prints DRILL OK, and always removes what it created.
#
#   scripts/hosting-restore-drill.sh /opt/abb/backups/abb-20261019T023000Z
#   scripts/hosting-restore-drill.sh b2:my-bucket/abb/abb-20261019T023000Z     (needs rclone and its config)
#   scripts/hosting-restore-drill.sh --latest                                  the newest COMPLETE bundle at ABB_OFFSITE_REMOTE
#
# Same configuration as hosting-backup.sh (/etc/abb/backup.env). The production database is never written: the restore target
# is the database `abb_restore_drill` next to it on the same server, created and dropped here, and the tool itself refuses a
# non-empty target. Exit codes: 0 DRILL OK; 1 the drill failed (the message says which step); 2 usage or configuration.
set -euo pipefail

CONF="${ABB_BACKUP_ENV:-/etc/abb/backup.env}"
# shellcheck disable=SC1090
if [ -r "$CONF" ]; then set -a; . "$CONF"; set +a; fi
ABB_DIR="${ABB_DIR:-/opt/abb}"
BACKUP_DIR="${ABB_BACKUP_DIR:-$ABB_DIR/backups}"
PASSFILE="${ABB_BACKUP_PASSPHRASE_FILE:-/etc/abb/backup.pass}"
REMOTE="${ABB_OFFSITE_REMOTE:-}"
TEXTFILE_DIR="${ABB_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
DRILL_DB="${ABB_DRILL_DATABASE:-abb_restore_drill}"
PROJECT="${ABB_COMPOSE_PROJECT:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB=(python3 "$HERE/hosting_backup_lib.py")

log() { printf 'hosting-restore-drill: %s\n' "$*"; }
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
fail() { printf 'DRILL FAILED: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || usage
case "$1" in -h|--help) usage ;; esac
SOURCE="$1"
case "$DRILL_DB" in *[!a-z0-9_]*|"") fail "ABB_DRILL_DATABASE must be lowercase letters, digits and underscores" ;; esac
[ "$DRILL_DB" != abb ] || fail "the drill database must not be the production database"
[ -r "$PASSFILE" ] || { echo "the passphrase file $PASSFILE is not readable" >&2; exit 2; }
perm="$(stat -c '%a' "$PASSFILE" 2>/dev/null || stat -f '%Lp' "$PASSFILE")"
[ "$perm" = 600 ] || [ "$perm" = 400 ] || { echo "the passphrase file must be mode 0600 (is $perm)" >&2; exit 2; }

cd "$ABB_DIR"
export ABB_BACKUP_DIR="$BACKUP_DIR"
compose_args=()
[ -n "$PROJECT" ] && compose_args+=(-p "$PROJECT")
[ -n "${ABB_COMPOSE_ENV_FILE:-}" ] && compose_args+=(--env-file "$ABB_COMPOSE_ENV_FILE")
# shellcheck source=hosting-compose.sh
. "$HERE/hosting-compose.sh"; abb_compose_resolve
for f in $compose_files; do compose_args+=(-f "$f"); done
dc() { docker compose "${compose_args[@]}" "${compose_profiles[@]}" "$@"; }
host_user="$(id -u):$(id -g)"
ABB_BACKUP_PASSPHRASE="$(cat "$PASSFILE")"
export ABB_BACKUP_PASSPHRASE

mkdir -p "$BACKUP_DIR"
SCRATCH="$(umask 077 && mktemp -d "$BACKUP_DIR/.drill.XXXXXX")"
db_created=0
cleanup() {
  local status=$?
  if [ "$db_created" = 1 ]; then
    dc exec -T postgres psql -U abb -d postgres -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS \"$DRILL_DB\" WITH (FORCE)" >/dev/null 2>&1 \
      || log "WARNING: could not drop $DRILL_DB; drop it by hand"
  fi
  rm -rf "$SCRATCH"
  if [ "$status" -ne 0 ]; then log "cleaned up (database dropped, scratch directory removed)"; fi
}
trap cleanup EXIT

# ---- 1. get the bundle ------------------------------------------------------------------------------------------------------
if [ "$SOURCE" = "--latest" ]; then
  [ -n "$REMOTE" ] || fail "--latest needs ABB_OFFSITE_REMOTE"
  latest="$(rclone lsf -R --files-only "$REMOTE" | "${LIB[@]}" latest-complete)" || fail "no complete bundle at the remote"
  SOURCE="$REMOTE/$latest"
fi
case "$SOURCE" in
  *:*) # remote:path
    command -v rclone >/dev/null || { echo "rclone is not installed" >&2; exit 2; }
    log "step=download source=${SOURCE%%:*}"
    rclone lsf "$SOURCE/COMPLETE" >/dev/null 2>&1 || fail "the remote bundle has no COMPLETE marker (an interrupted upload?)"
    rclone copy "$SOURCE" "$SCRATCH/bundle" --checksum --retries 3 --timeout 120s || fail "download failed"
    rm -f "$SCRATCH/bundle/COMPLETE"
    BUNDLE="$SCRATCH/bundle"
    ;;
  *)
    [ -f "$SOURCE/MANIFEST.json" ] || fail "$SOURCE is not a backup bundle (no MANIFEST.json)"
    BUNDLE="$(cd "$SOURCE" && pwd)"
    ;;
esac

# ---- 2. verify (touches no database) ----------------------------------------------------------------------------------------
log "step=verify"
dc run --rm -T --user "$host_user" -e ABB_BACKUP_PASSPHRASE -v "$BUNDLE:/bundle:ro" backup verify /bundle --passphrase-env ABB_BACKUP_PASSPHRASE \
  >"$SCRATCH/verify.out" 2>"$SCRATCH/verify.err" || { sed 's/^/  /' "$SCRATCH/verify.err" >&2; fail "the bundle does not verify (wrong passphrase, tampering or corruption)"; }

# ---- 3. a throwaway database ------------------------------------------------------------------------------------------------
log "step=create-database name=$DRILL_DB"
exists="$(dc exec -T postgres psql -U abb -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DRILL_DB'")" || fail "the database server is not reachable"
[ -z "$exists" ] || fail "a database named $DRILL_DB already exists; refusing to touch it (drop it if it is a leftover)"
dc exec -T postgres psql -U abb -d postgres -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"$DRILL_DB\" OWNER abb" || fail "could not create the drill database"
db_created=1
mkdir -p "$SCRATCH/artifacts" "$SCRATCH/work"

# ---- 4. restore with the real tool ------------------------------------------------------------------------------------------
# ABB_RESTORE_DATABASE_URL is derived inside the container from the owner URL it already has, so no password passes through here.
log "step=restore"
# shellcheck disable=SC2016 # expanded by the container's shell, on purpose
dc run --rm -T --user "$host_user" -e ABB_BACKUP_PASSPHRASE -v "$BUNDLE:/bundle:ro" -v "$SCRATCH:/scratch" --entrypoint sh backup -c \
  'ABB_RESTORE_DATABASE_URL="${ABB_BACKUP_DATABASE_URL%/*}/$0" exec python -m abb_api.backup restore /bundle --artifact-dir /scratch/artifacts --workdir /scratch/work --passphrase-env ABB_BACKUP_PASSPHRASE' \
  "$DRILL_DB" >"$SCRATCH/restore.out" 2>"$SCRATCH/restore.err" || { sed 's/^/  /' "$SCRATCH/restore.err" >&2; fail "the restore failed"; }

# ---- 5. compare the restored database with the manifest, table by table -----------------------------------------------------
log "step=compare"
total=0; bad=0; checked=0
# stdin of the loop is the manifest listing: every command inside must take its own (docker compose exec reads stdin).
while read -r table expected; do
  actual="$(dc exec -T postgres psql -U abb -d "$DRILL_DB" -tAc "SELECT count(*) FROM \"$table\"" </dev/null)" || fail "could not count $table"
  checked=$((checked + 1)); total=$((total + actual))
  if [ "$actual" != "$expected" ]; then log "MISMATCH $table manifest=$expected restored=$actual"; bad=$((bad + 1)); fi
done < <("${LIB[@]}" manifest-counts "$BUNDLE")
[ "$checked" -gt 0 ] || fail "the manifest listed no tables"
[ "$bad" -eq 0 ] || fail "$bad table(s) differ from the manifest"
files="$(find "$SCRATCH/artifacts" -type f | wc -l | tr -d ' ')"
summary="$(tr '\n' ' ' <"$SCRATCH/restore.out")"

# ---- 6. record, clean up, report --------------------------------------------------------------------------------------------
if [ -d "$TEXTFILE_DIR" ] || mkdir -p "$TEXTFILE_DIR" 2>/dev/null; then
  "${LIB[@]}" metrics --file "$TEXTFILE_DIR/abb_restore_drill.prom" \
    "abb_hosting_restore_drill_last_success_timestamp_seconds=$(date +%s)" "abb_hosting_restore_drill_tables_checked=$checked" 2>/dev/null \
    || log "WARNING: could not write the drill metric"
fi
log "restore: $summary"
printf 'DRILL OK  tables=%s rows=%s artifact_files=%s database=%s (dropped) source=%s\n' "$checked" "$total" "$files" "$DRILL_DB" "${SOURCE%%:*}"
