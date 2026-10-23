# Sourced by hosting-backup.sh and hosting-restore-drill.sh: the rclone configuration both need (live bugs 2 and 3 of the first
# Hetzner deployment). The systemd unit sets RCLONE_CONFIG; an interactive run does not, and rclone then looks in ~/.config and says
# "didn't find section in config file". And a file made with `sudo rclone config` is root:root 0600, which the service user
# (deploy) cannot read: rclone reports only "permission denied", so this names the cause.
#
#   abb_rclone_default   RCLONE_CONFIG unset and /etc/abb/rclone.conf exists -> export it (backup.env or the caller's value wins)
#   abb_rclone_diagnose  returns 1 and prints the cause and the fix when RCLONE_CONFIG is set but missing or unreadable by this user
abb_rclone_default() {
  local def="${ABB_RCLONE_DEFAULT_CONF:-/etc/abb/rclone.conf}"
  if [ -z "${RCLONE_CONFIG:-}" ] && [ -e "$def" ]; then export RCLONE_CONFIG="$def"; fi
  return 0
}
abb_rclone_diagnose() {
  local f="${RCLONE_CONFIG:-}" me owner
  [ -n "$f" ] || return 0
  if [ ! -e "$f" ]; then
    printf 'RCLONE_CONFIG=%s does not exist (create it as the deploy user: rclone --config %s config)\n' "$f" "$f"
    return 1
  fi
  [ -r "$f" ] && return 0
  me="$(id -un)"; owner="$(stat -c '%U' "$f" 2>/dev/null || stat -f '%Su' "$f" 2>/dev/null || echo '?')"
  printf 'the rclone config %s is not readable by %s (it is owned by %s: it was probably made with sudo). Fix: sudo chown %s:%s %s && sudo chmod 600 %s\n' \
    "$f" "$me" "$owner" "$me" "$(id -gn)" "$f" "$f"
  return 1
}
