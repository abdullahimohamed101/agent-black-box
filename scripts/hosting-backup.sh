#!/usr/bin/env bash
# Nightly backup of a hosted Agent BlackBox (Phase 23, ADR-178, D6): an ENCRYPTED bundle from the existing backup tool, verified,
# copied to a SECOND provider with rclone, checked there, old copies pruned under a "never delete the newest N" rule, and a
# textfile metric written for node_exporter. Run by abb-backup.timer (deploy/hosting/systemd) as the deploy user.
#
#   scripts/hosting-backup.sh            one full cycle
#   scripts/hosting-backup.sh --check    first-run / freshness check (KI-243), no backup; exit 1 when the last complete backup is
#                                        missing or older than ABB_BACKUP_MAX_AGE_HOURS (36). hosting-verify.sh --server runs it.
#
# Configuration: environment, usually /etc/abb/backup.env (mode 0600, not in git; template deploy/hosting/systemd/backup.env.example).
# Nothing secret is printed: no passphrase, no rclone credential, no database URL. The passphrase never reaches a command line.
set -euo pipefail

CONF="${ABB_BACKUP_ENV:-/etc/abb/backup.env}"
# shellcheck disable=SC1090
if [ -r "$CONF" ]; then set -a; . "$CONF"; set +a; fi

ABB_DIR="${ABB_DIR:-/opt/abb}"
BACKUP_DIR="${ABB_BACKUP_DIR:-$ABB_DIR/backups}"
PASSFILE="${ABB_BACKUP_PASSPHRASE_FILE:-/etc/abb/backup.pass}"
REMOTE="${ABB_OFFSITE_REMOTE:-}"                      # e.g. b2:my-bucket/abb  (rclone remote:path)
TEXTFILE_DIR="${ABB_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
METRICS_FILE="$TEXTFILE_DIR/abb_backup.prom"
LOCAL_KEEP_DAYS="${ABB_LOCAL_KEEP_DAYS:-7}"
LOCAL_KEEP_MIN="${ABB_LOCAL_KEEP_MIN:-3}"
REMOTE_KEEP_DAYS="${ABB_OFFSITE_KEEP_DAYS:-30}"
REMOTE_KEEP_MIN="${ABB_OFFSITE_KEEP_MIN:-7}"
REMOTE_PRUNE="${ABB_OFFSITE_PRUNE:-1}"                # 0 when the provider key cannot delete: use the provider's lifecycle rule
MAX_AGE_HOURS="${ABB_BACKUP_MAX_AGE_HOURS:-36}"
PROJECT="${ABB_COMPOSE_PROJECT:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB=(python3 "$HERE/hosting_backup_lib.py")

log() { printf 'hosting-backup: %s\n' "$*"; }
die() { printf 'hosting-backup: ERROR: %s\n' "$*" >&2; exit "${2:-1}"; }

if [ "${1:-}" = "--check" ]; then
  "${LIB[@]}" check --file "$METRICS_FILE" --max-age-hours "$MAX_AGE_HOURS"
  exit $?
fi
[ $# -eq 0 ] || die "usage: hosting-backup.sh [--check]" 2

# ---- compose plumbing -------------------------------------------------------------------------------------------------------
cd "$ABB_DIR"
export ABB_BACKUP_DIR="$BACKUP_DIR"
compose_args=()
[ -n "$PROJECT" ] && compose_args+=(-p "$PROJECT")
[ -n "${ABB_COMPOSE_ENV_FILE:-}" ] && compose_args+=(--env-file "$ABB_COMPOSE_ENV_FILE")
for f in ${ABB_COMPOSE_FILES:-docker-compose.yml deploy/hosting/compose.production.yml}; do compose_args+=(-f "$f"); done
dc() { docker compose "${compose_args[@]}" --profile ops "$@"; }
# A short root-in-container step with FIXED arguments (never the backup tool): the artifact files are 0600 owned by uid 10001, so
# the tool must run as that user, and the bundle it makes (0700, uid 10001) is then handed to the deploy user for rclone.
root_sh() { dc run --rm -T --no-deps --user 0:0 --entrypoint sh backup -c "$@"; }

started_at="$(date +%s)"
NAME="$("${LIB[@]}" name)"
STAGING=".staging"
success=0
local_ok=0
cleanup() {
  local status=$?
  rmdir "$LOCKDIR" 2>/dev/null || true
  if [ "$success" != 1 ]; then
    # A failed run keeps the previous success time (the age alert is what pages); it records that this run failed.
    prior=0
    if [ -r "$METRICS_FILE" ]; then
      prior="$(awk '$1=="abb_hosting_backup_last_success_timestamp_seconds"{print $2}' "$METRICS_FILE" 2>/dev/null || echo 0)"
      prior_off="$(awk '$1=="abb_hosting_backup_offsite_last_success_timestamp_seconds"{print $2}' "$METRICS_FILE" 2>/dev/null || echo 0)"
    fi
    "${LIB[@]}" metrics --file "$METRICS_FILE" \
      "abb_hosting_backup_last_success_timestamp_seconds=${prior:-0}" \
      "abb_hosting_backup_offsite_last_success_timestamp_seconds=${prior_off:-0}" \
      "abb_hosting_backup_last_run_timestamp_seconds=$(date +%s)" \
      "abb_hosting_backup_last_run_success=0" 2>/dev/null || true
    # A bundle that already verified is kept (it is a good backup that only missed the second provider); anything earlier is removed.
    [ "$local_ok" = 1 ] || rm -rf "${BACKUP_DIR:?}/$NAME" 2>/dev/null || true
    log "FAILED (exit $status); nothing was recorded as a success"
  fi
}
LOCKDIR=""

# ---- preflight --------------------------------------------------------------------------------------------------------------
command -v docker >/dev/null || die "docker is not installed" 2
command -v rclone >/dev/null || die "rclone is not installed (scripts/hosting-bootstrap.sh installs it)" 2
[ -n "$REMOTE" ] || die "ABB_OFFSITE_REMOTE is not set (rclone remote:path of the SECOND provider)" 2
case "$REMOTE" in /*|.*) die "ABB_OFFSITE_REMOTE must be an rclone remote:path, not a local path (a copy on the same disk is not a backup)" 2 ;; esac
[ -r "$PASSFILE" ] || die "the passphrase file $PASSFILE is not readable" 2
perm="$(stat -c '%a' "$PASSFILE" 2>/dev/null || stat -f '%Lp' "$PASSFILE")"
[ "$perm" = 600 ] || [ "$perm" = 400 ] || die "the passphrase file must be mode 0600 (is $perm)" 2
[ "$(wc -c <"$PASSFILE")" -ge 12 ] || die "the passphrase must be at least 12 bytes" 2
mkdir -p "$BACKUP_DIR"
LOCKDIR="$BACKUP_DIR/.lock"
mkdir "$LOCKDIR" 2>/dev/null || die "another backup is running (or died: remove $LOCKDIR)" 75
trap cleanup EXIT

ABB_BACKUP_PASSPHRASE="$(cat "$PASSFILE")"
export ABB_BACKUP_PASSPHRASE
host_user="$(id -u):$(id -g)"

# ---- 1. create (encrypted) --------------------------------------------------------------------------------------------------
log "step=create bundle=$NAME"
root_sh "install -d -o 10001 -g 10001 -m 0700 /backups/$STAGING && rm -rf /backups/$STAGING/$NAME" sh
dc run --rm -T -e ABB_BACKUP_PASSPHRASE backup create --out "/backups/$STAGING/$NAME" --encrypt --passphrase-env ABB_BACKUP_PASSPHRASE >/dev/null
root_sh "mv /backups/$STAGING/$NAME /backups/$NAME && chown -R $host_user /backups/$NAME" sh
bundle="$BACKUP_DIR/$NAME"
bytes="$(du -sk "$bundle" | awk '{print $1 * 1024}')"

# ---- 2. verify the local bundle ---------------------------------------------------------------------------------------------
log "step=verify-local"
dc run --rm -T --user "$host_user" -e ABB_BACKUP_PASSPHRASE -v "$bundle:/bundle:ro" backup verify /bundle --passphrase-env ABB_BACKUP_PASSPHRASE >/dev/null
local_ok=1

# ---- 3. copy to the second provider, then check it there --------------------------------------------------------------------
log "step=upload remote=${REMOTE%%:*}"
rclone copy "$bundle" "$REMOTE/$NAME" --checksum --transfers 2 --retries 3 --low-level-retries 5 --contimeout 30s --timeout 120s
rclone check "$bundle" "$REMOTE/$NAME" --one-way >/dev/null 2>&1 || die "the remote copy does not match the local bundle"
# COMPLETE is written LAST: a bundle without it is an interrupted upload and is never relied on or counted (prune rules).
manifest_sha="$("${LIB[@]}" sha256 "$bundle/MANIFEST.json")"
printf '%s %s\n' "$manifest_sha" "$NAME" | rclone rcat "$REMOTE/$NAME/COMPLETE"
# Remote verify with the real tool: download the manifest + files? That is the restore drill's job (monthly). Here the
# provider-side checksum comparison above is the check; a full download of every bundle nightly would cost egress.
offsite_done="$(date +%s)"

# ---- 4. prune ---------------------------------------------------------------------------------------------------------------
prune_local() {
  local listing
  listing="$(cd "$BACKUP_DIR" && for d in abb-*; do if [ -d "$d" ]; then printf '%s/COMPLETE\n' "$d"; fi; done)"
  # local bundles are complete once verified; this run's is the required anchor
  printf '%s\n' "$listing" | "${LIB[@]}" prune-plan --keep-days "$LOCAL_KEEP_DAYS" --keep-min "$LOCAL_KEEP_MIN" --require "$NAME" |
    while read -r old; do
      [ -n "$old" ] || continue
      log "prune local=$old"
      rm -rf "${BACKUP_DIR:?}/$old"
    done
}
prune_remote() {
  local listing victims
  listing="$(rclone lsf -R --files-only "$REMOTE")" || { log "WARNING: remote listing failed; not pruning"; return 0; }
  victims="$(printf '%s\n' "$listing" | "${LIB[@]}" prune-plan --keep-days "$REMOTE_KEEP_DAYS" --keep-min "$REMOTE_KEEP_MIN" --require "$NAME")" \
    || { log "WARNING: the prune safety rules refused; nothing deleted"; return 0; }
  for old in $victims; do
    log "prune remote=$old"
    rclone purge "$REMOTE/$old" || log "WARNING: could not delete $old (does the key allow delete? use the provider lifecycle rule and set ABB_OFFSITE_PRUNE=0)"
  done
}
prune_local || log "WARNING: local pruning failed or was refused by its safety rules; nothing more was deleted"
if [ "$REMOTE_PRUNE" = 1 ]; then prune_remote; else log "remote pruning off (ABB_OFFSITE_PRUNE=0): rely on the provider's lifecycle rule"; fi

# ---- 5. metrics, dead-man ping ----------------------------------------------------------------------------------------------
remote_count="$(rclone lsf -R --files-only "$REMOTE" 2>/dev/null | grep -c '/COMPLETE$' || true)"
finished="$(date +%s)"
"${LIB[@]}" metrics --file "$METRICS_FILE" \
  "abb_hosting_backup_last_success_timestamp_seconds=$finished" \
  "abb_hosting_backup_offsite_last_success_timestamp_seconds=$offsite_done" \
  "abb_hosting_backup_last_run_timestamp_seconds=$finished" \
  "abb_hosting_backup_last_run_success=1" \
  "abb_hosting_backup_last_duration_seconds=$((finished - started_at))" \
  "abb_hosting_backup_last_bundle_bytes=$bytes" \
  "abb_hosting_backup_offsite_bundles=${remote_count:-0}"
success=1
if [ -n "${ABB_DEADMAN_URL:-}" ]; then
  # The ping is an alert channel, not part of the backup: a failure here is logged, never fatal. The URL is a secret-ish token.
  curl -fsS -m 10 --retry 2 -o /dev/null "$ABB_DEADMAN_URL" || log "WARNING: the dead-man ping failed"
fi
log "OK bundle=$NAME bytes=$bytes offsite_bundles=${remote_count:-0} seconds=$((finished - started_at))"
