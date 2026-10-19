#!/usr/bin/env bash
# Generates the secrets of a hosted Agent BlackBox (Phase 23, ADR-178) into files only the deploy user can read. Nothing is ever
# printed, logged or put on a command line: values go from `openssl rand` straight into a mode-0600 file created with an exclusive
# create, so an existing file is never overwritten (and re-running is safe: it reports what it kept).
#
#   scripts/hosting-secrets.sh --origin https://abb.example.org --acme-email you@example.org
#       [--dir /opt/abb] [--etc-dir /etc/abb] [--owner deploy] [--template .env.production.example]
#
# Creates, unless the file exists already:
#   <dir>/.env                          from .env.production.example: the three PostgreSQL role passwords and ABB_METRICS_TOKEN are
#                                       generated; the origin and ACME email come from the flags; the Google OIDC client id and secret
#                                       stay EMPTY: enter them yourself in an editor (they are never generated, passed or pasted into chat)
#   <dir>/secrets/metrics_token         the same token, as a file for Prometheus
#   <dir>/secrets/grafana_admin_password
#   <dir>/secrets/alertmanager/          (empty, 0700) for the notification receiver's own secret files (SMTP password, webhook URLs)
#   <etc-dir>/backup.pass               the backup encryption passphrase. NEVER regenerated: a new one would make every existing bundle
#                                       unreadable. Copy it to your password manager now (sudo cat it yourself; this script will not).
#   <etc-dir>/backup.env                the non-secret backup settings, from deploy/hosting/systemd/backup.env.example
# PostgreSQL stores the first password when it creates its volume: changing the .env later does not change the database role.
set -euo pipefail
umask 077

DIR=/opt/abb; ETC=/etc/abb; OWNER=deploy; ORIGIN=""; EMAIL=""
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
TEMPLATE="$REPO/.env.production.example"
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
die() { printf 'hosting-secrets: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --origin) [ $# -ge 2 ] || usage; ORIGIN="$2"; shift ;;
    --acme-email) [ $# -ge 2 ] || usage; EMAIL="$2"; shift ;;
    --dir) [ $# -ge 2 ] || usage; DIR="$2"; shift ;;
    --etc-dir) [ $# -ge 2 ] || usage; ETC="$2"; shift ;;
    --owner) [ $# -ge 2 ] || usage; OWNER="$2"; shift ;;
    --template) [ $# -ge 2 ] || usage; TEMPLATE="$2"; shift ;;
    *) usage ;;
  esac
  shift
done
if [ -z "$ORIGIN" ] || [ -z "$EMAIL" ]; then usage; fi
printf '%s' "$ORIGIN" | grep -Eq '^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]+)?$' || die "--origin must be https://host with no path and no trailing slash" 2
printf '%s' "$EMAIL" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' || die "--acme-email is not an email address" 2
[ -r "$TEMPLATE" ] || die "template $TEMPLATE not found" 2
command -v openssl >/dev/null || die "openssl is required" 2

owned() { # hand a file to the deploy user when running as root
  if [ "$(id -u)" -eq 0 ] && id "$OWNER" >/dev/null 2>&1; then chown "$OWNER:$OWNER" "$@"; fi
}
mkdir_private() { [ -d "$1" ] || mkdir -p "$1"; chmod 0700 "$1"; owned "$1"; }
rand_hex() { openssl rand -hex "$1"; }

# create_exclusive FILE : stdin becomes the file, mode 0600, only if it does not exist (noclobber makes the create atomic).
create_exclusive() {
  local file="$1"
  if [ -e "$file" ]; then printf 'KEPT     %s (exists; never overwritten)\n' "$file"; cat >/dev/null; return 0; fi
  ( set -C; cat >"$file" ) 2>/dev/null || die "could not create $file exclusively"
  chmod 0600 "$file"; owned "$file"
  printf 'CREATED  %s (mode 0600)\n' "$file"
}

mkdir_private "$DIR"; mkdir_private "$DIR/secrets"; mkdir_private "$DIR/secrets/alertmanager"; mkdir_private "$ETC"
[ "$DIR" = /opt/abb ] && chmod 0755 "$DIR"   # the checkout itself is not secret; .env and secrets/ are

# One token, written to the .env and to secrets/metrics_token. It lives in a shell variable for the length of this script only.
METRICS_TOKEN="$(rand_hex 32)"
if [ -e "$DIR/.env" ]; then
  # The file exists: the token that matches it is already there. Do not write a second, different one beside it.
  METRICS_TOKEN=""
fi

env_from_template() {
  local line key
  while IFS= read -r line || [ -n "$line" ]; do
    key="${line%%=*}"
    case "$key" in
      ABB_PUBLIC_ORIGIN) printf 'ABB_PUBLIC_ORIGIN=%s\n' "$ORIGIN" ;;
      ABB_ACME_EMAIL) printf 'ABB_ACME_EMAIL=%s\n' "$EMAIL" ;;
      ABB_POSTGRES_PASSWORD|ABB_RUNTIME_DB_PASSWORD|ABB_RETENTION_DB_PASSWORD) printf '%s=%s\n' "$key" "$(rand_hex 24)" ;;
      ABB_METRICS_TOKEN) printf 'ABB_METRICS_TOKEN=%s\n' "$METRICS_TOKEN" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$TEMPLATE"
}

env_from_template | create_exclusive "$DIR/.env"
if [ -n "$METRICS_TOKEN" ]; then
  printf '%s' "$METRICS_TOKEN" | create_exclusive "$DIR/secrets/metrics_token"
else
  if [ -e "$DIR/secrets/metrics_token" ]; then
    printf 'KEPT     %s (exists; never overwritten)\n' "$DIR/secrets/metrics_token"
  else
    printf 'NOTE     %s exists but secrets/metrics_token does not: copy its ABB_METRICS_TOKEN value there yourself (mode 0600)\n' "$DIR/.env"
  fi
fi
METRICS_TOKEN=""
rand_hex 16 | create_exclusive "$DIR/secrets/grafana_admin_password"
openssl rand -base64 33 | create_exclusive "$ETC/backup.pass"
if [ -r "$REPO/deploy/hosting/systemd/backup.env.example" ]; then
  create_exclusive "$ETC/backup.env" <"$REPO/deploy/hosting/systemd/backup.env.example"
fi

# Names only: which keys the owner still has to fill in.
empty="$(grep -E '^ABB_[A-Z0-9_]+=$' "$DIR/.env" 2>/dev/null | cut -d= -f1 | tr '\n' ' ' || true)"
[ -z "$empty" ] || printf 'TODO     enter these in %s with your editor: %s\n' "$DIR/.env" "$empty"
printf 'TODO     copy %s/backup.pass to your password manager now (sudo cat it yourself); without it no backup can be restored\n' "$ETC"
printf 'TODO     put the rclone remote for the second provider in %s/rclone.conf (mode 0600) and set ABB_OFFSITE_REMOTE in %s/backup.env\n' "$ETC" "$ETC"
