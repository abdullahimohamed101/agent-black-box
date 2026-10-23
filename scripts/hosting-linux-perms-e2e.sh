#!/usr/bin/env bash
# Linux uid/mode semantics for the hosting backup (KI-313; the first live Hetzner deployment found three bugs that the macOS Docker
# Desktop rigs could not see, because Docker Desktop does not enforce uid and mode on bind mounts). Here the permissions live in a
# Docker VOLUME (a Linux filesystem inside the VM), so a real Linux kernel enforces them even on a Mac.
#
# Proves, with the host layout exactly as scripts/hosting-bootstrap.sh makes it (/opt/abb/backups 0700 uid 1000, .staging 0700 uid 10001):
#   1. CONTROL (the live bug): a container as uid 10001 cannot reach .staging through the 0700 mount root.
#   2. the shape hosting-backup.sh uses (only .staging mounted) lets uid 10001 write the bundle, with the root left 0700.
#   3. an rclone.conf that is root:root 0600 is diagnosed by name for the service user (uid 1000) and is fine once chowned;
#      the default RCLONE_CONFIG helper works under the real mode rules.
# NOT proven here (UNVERIFIED (env)): hosting-backup.sh itself end to end on Linux (it needs a Linux host: the GitHub ubuntu runner
# follow-up in KI-313). Needs Docker 26+ (volume-subpath). No heavy services: seconds.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
UBUNTU='ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac'
V="abb-perms-$$-bk"; C="abb-perms-$$-cfg"
fail() { echo "HOSTING-LINUX-PERMS-E2E FAILED: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
cleanup() { docker volume rm -f "$V" "$C" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker info >/dev/null 2>&1 || fail "docker is not reachable"
run() { docker run --rm -i "$@"; }

step "lay out /opt/abb/backups the way the bootstrap does"
docker volume create "$V" >/dev/null; docker volume create "$C" >/dev/null
run -v "$V:/b" --entrypoint sh "$UBUNTU" -c 'install -d -m 0700 -o 1000 -g 1000 /b && install -d -m 0700 -o 10001 -g 10001 /b/.staging && install -d -m 0700 -o 1000 -g 1000 /b/abb-bundle' </dev/null \
  || fail "could not lay out the volume"

step "1. control: uid 10001 through the 0700 uid-1000 root (the live bug) must be refused"
if out="$(run --user 10001 -v "$V:/backups" --entrypoint sh "$UBUNTU" -c 'ls /backups/.staging' 2>&1 </dev/null)"; then
  fail "uid 10001 could walk a 0700 uid-1000 directory: this Docker does not enforce modes, the rig proves nothing"
fi
case "$out" in *"Permission denied"*) echo "ok: refused ($out)" ;; *) fail "refused for another reason: $out" ;; esac

step "2. only .staging mounted (what hosting-backup.sh does): uid 10001 writes, and cannot see the bundles beside it"
run --user 10001 --mount "type=volume,src=$V,dst=/staging,volume-subpath=.staging" --entrypoint sh "$UBUNTU" -c \
  'mkdir /staging/abb-x && echo data >/staging/abb-x/f && test -f /staging/abb-x/f' </dev/null || fail "uid 10001 cannot write the mounted .staging"
run --user 10001 --mount "type=volume,src=$V,dst=/staging,volume-subpath=.staging" --entrypoint sh "$UBUNTU" -c '! test -e /staging/abb-bundle' </dev/null \
  || fail "the bundle directory is visible inside the create container"
mode="$(run -v "$V:/b" --entrypoint sh "$UBUNTU" -c 'stat -c "%a %u" /b' </dev/null)"
[ "$mode" = "700 1000" ] || fail "the backups root changed: [$mode] (want 700 1000)"

step "3. rclone.conf made by root (sudo rclone config): diagnosed for the service user, fine once chowned"
run -v "$C:/etc/abb" --entrypoint sh "$UBUNTU" -c 'install -d -m 0700 -o 1000 -g 1000 /etc/abb && printf "[b2]\ntype = local\n" >/etc/abb/rclone.conf && chown root:root /etc/abb/rclone.conf && chmod 600 /etc/abb/rclone.conf' </dev/null
as_deploy() { run --user 1000 -v "$C:/etc/abb" -v "$root/scripts:/s:ro" -e ABB_RCLONE_DEFAULT_CONF=/etc/abb/rclone.conf --entrypoint bash "$UBUNTU" -c ". /s/hosting-env.sh; $1" </dev/null; }
if msg="$(as_deploy 'abb_rclone_default; abb_rclone_diagnose' 2>&1)"; then fail "a root-owned 0600 config passed the diagnosis for uid 1000"; fi
case "$msg" in *"not readable by ubuntu"*"owned by root"*"chown"*) echo "ok: $msg" ;; *) fail "the diagnosis does not name the cause: $msg" ;; esac
run -v "$C:/etc/abb" --entrypoint sh "$UBUNTU" -c 'chown 1000:1000 /etc/abb/rclone.conf' </dev/null
as_deploy 'abb_rclone_default; abb_rclone_diagnose; [ "$RCLONE_CONFIG" = /etc/abb/rclone.conf ] && cat "$RCLONE_CONFIG" >/dev/null' >/dev/null \
  || fail "after chown deploy:deploy the config is still refused or the default was not applied"

echo
echo "HOSTING-LINUX-PERMS-E2E OK"
