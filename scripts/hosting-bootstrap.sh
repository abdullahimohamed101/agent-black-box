#!/usr/bin/env bash
# Host bootstrap for a fresh Ubuntu 24.04 server (Hetzner Cloud VM) that will run Agent BlackBox behind a shared Caddy
# (Phase 23, ADR-178). Idempotent: every step first tests whether its end state already holds, so a second run changes nothing.
#
#   sudo scripts/hosting-bootstrap.sh --check                          list every action, change nothing (also --dry-run)
#   sudo scripts/hosting-bootstrap.sh --apply --ssh-key-file KEY.pub   stage 1: everything EXCEPT hardening ssh
#   sudo scripts/hosting-bootstrap.sh --harden-ssh                     stage 2: key-only ssh, only after a key login is proven
#
# LOCKOUT-SAFE SEQUENCE. Stage 1 installs the deploy user and your public key but leaves password login on. You then open a SECOND
# terminal and log in as deploy WITH THE KEY (ssh -i KEY deploy@SERVER), leave that session open, and only then run stage 2 from
# the first. Stage 2 refuses unless it finds that evidence (a `deploy` publickey login in the ssh log AND a live deploy session),
# validates the new configuration with `sshd -t` and `sshd -T` before reloading (a reload keeps existing sessions), and puts the old
# state back if sshd stops listening. If you are ever locked out: the Hetzner console (rescue/VNC) works without ssh.
#
# What stage 1 does: packages (ufw, fail2ban, unattended-upgrades, rclone, git, ...); Docker Engine from Docker's own apt repo,
# GPG fingerprint checked, `docker-ce` pinned to one release series (ABB_DOCKER_SERIES, default 5:29.*) so an upgrade is deliberate;
# /etc/docker/daemon.json (json-file log rotation, live-restore); the `deploy` user (no password, docker group, passwordless sudo:
# docker-group membership is root-equivalent anyway); firewall ufw (default deny; 22/tcp rate-limited, 80/tcp, 443/tcp; 443/udp only
# with ABB_HTTP3=1); fail2ban sshd jail; unattended security upgrades with a 04:30 reboot only when one is required
# (ABB_AUTO_REBOOT=0 turns it off); a 2 GiB swapfile; NTP; the directories under /opt and /etc/abb; the Docker network `edge`;
# the abb-backup systemd units (installed, NOT enabled: enable them after /etc/abb/backup.env is configured).
#
# DOCKER BYPASSES ufw: a container's `ports:` mapping is published through iptables rules that run before ufw's, so ufw cannot
# block it. The control is that no ABB container publishes a port (only Caddy publishes 80/443, ADR-178);
# `scripts/hosting-verify.sh --server` checks `docker ps`, and the Hetzner Cloud Firewall (outside the host) is a second layer.
set -euo pipefail

MODE=""
KEY_FILE=""
DEPLOY_USER="${ABB_DEPLOY_USER:-deploy}"
DOCKER_SERIES="${ABB_DOCKER_SERIES:-5:29.*}"
DOCKER_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"   # https://docs.docker.com/engine/install/ubuntu/ ; checked, not trusted blindly
SWAP_MB="${ABB_SWAP_MB:-2048}"
HTTP3="${ABB_HTTP3:-0}"
AUTO_REBOOT="${ABB_AUTO_REBOOT:-1}"
EVIDENCE_HOURS="${ABB_SSH_EVIDENCE_HOURS:-24}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PACKAGES=(ca-certificates curl gnupg ufw fail2ban unattended-upgrades rclone git openssl python3 sudo openssh-server iproute2)
pending=0

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
die() { printf 'hosting-bootstrap: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
say() { printf '%s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check|--dry-run) MODE=check ;;
    --apply) MODE=apply ;;
    --harden-ssh) MODE=ssh ;;
    --ssh-key-file) [ $# -ge 2 ] || usage; KEY_FILE="$2"; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done
[ -n "$MODE" ] || usage

# ---- preconditions ----------------------------------------------------------------------------------------------------------
if [ "$MODE" != check ] && [ "$(id -u)" -ne 0 ]; then die "run as root (sudo): --apply and --harden-ssh change the system" 2; fi
# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != "24.04" ]; then
  die "this script is written and tested for Ubuntu 24.04 (found ${PRETTY_NAME:-unknown})" 2
fi
case "$DEPLOY_USER" in *[!a-z0-9_-]*|"") die "ABB_DEPLOY_USER is not a plain user name" 2 ;; esac

# ---- the step runner --------------------------------------------------------------------------------------------------------
# step "description" state_fn apply_fn : state_fn succeeds when the end state already holds. In --check mode nothing is applied.
# After an apply the state is tested again: a step that did not reach its end state stops the script.
step() {
  local desc="$1" state="$2" apply="$3"
  if "$state" 2>/dev/null; then printf 'OK     %s\n' "$desc"; return 0; fi
  pending=$((pending + 1))
  if [ "$MODE" = check ]; then printf 'WOULD  %s\n' "$desc"; return 0; fi
  printf 'DO     %s\n' "$desc"
  "$apply"
  "$state" 2>/dev/null || die "step did not reach its end state: $desc"
}

home_of() { getent passwd "$DEPLOY_USER" | cut -d: -f6; }

# ---- 1. packages -------------------------------------------------------------------------------------------------------------
pkgs_missing() { local p; for p in "${PACKAGES[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || return 0; done; return 1; }
st_packages() { ! pkgs_missing; }
do_packages() { export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq --no-install-recommends "${PACKAGES[@]}"; }

# ---- 2. Docker Engine from the official repo, pinned to a release series ------------------------------------------------------
KEYRING=/etc/apt/keyrings/docker.asc
SOURCES=/etc/apt/sources.list.d/docker.list
PIN=/etc/apt/preferences.d/abb-docker
want_source() { printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$KEYRING" "${VERSION_CODENAME}"; }
want_pin() { printf '# Managed by scripts/hosting-bootstrap.sh: stay inside one Docker release series; change ABB_DOCKER_SERIES deliberately.\nPackage: docker-ce docker-ce-cli\nPin: version %s\nPin-Priority: 1001\n' "$DOCKER_SERIES"; }
# The fingerprints are captured first and matched afterwards: `... | grep -q` under pipefail can fail by SIGPIPE when grep leaves early.
key_has_fpr() { local fprs; fprs="$(gpg --show-keys --with-colons "$1" 2>/dev/null | awk -F: '$1=="fpr"{print $10}')"; grep -qx "$DOCKER_FPR" <<<"$fprs"; }
keyring_ok() { [ -f "$KEYRING" ] && key_has_fpr "$KEYRING"; }
st_docker_repo() { keyring_ok && [ "$(cat "$SOURCES" 2>/dev/null)" = "$(want_source)" ] && [ "$(cat "$PIN" 2>/dev/null)" = "$(want_pin)" ]; }
do_docker_repo() {
  install -m 0755 -d /etc/apt/keyrings
  local tmp; tmp="$(mktemp)"
  curl -fsSL --max-time 60 https://download.docker.com/linux/ubuntu/gpg -o "$tmp"
  key_has_fpr "$tmp" || { rm -f "$tmp"; die "the downloaded Docker signing key does not have the expected fingerprint $DOCKER_FPR"; }
  install -m 0644 "$tmp" "$KEYRING"; rm -f "$tmp"
  want_source >"$SOURCES"; want_pin >"$PIN"; chmod 0644 "$SOURCES" "$PIN"
  apt-get update -qq
}
docker_version() { dpkg-query -W -f='${Version}' docker-ce 2>/dev/null; }
# shellcheck disable=SC2254 # DOCKER_SERIES is a glob on purpose (5:29.*)
st_docker() { local v; v="$(docker_version)" || return 1; case "$v" in $DOCKER_SERIES) return 0 ;; *) return 1 ;; esac; }
do_docker() { export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; }

# ---- 3. daemon.json ----------------------------------------------------------------------------------------------------------
DAEMON=/etc/docker/daemon.json
daemon_merge() { # prints the merged JSON; keeps unrelated keys
  python3 - "$DAEMON" <<'PY'
import json, os, sys
path = sys.argv[1]
cur = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
cur["log-driver"] = "json-file"
cur["log-opts"] = {**cur.get("log-opts", {}), "max-size": "10m", "max-file": "3"}
cur["live-restore"] = True
print(json.dumps(cur, indent=2, sort_keys=True))
PY
}
st_daemon() { [ -f "$DAEMON" ] && [ "$(cat "$DAEMON")" = "$(daemon_merge)" ]; }
do_daemon() {
  install -m 0755 -d /etc/docker
  daemon_merge >"$DAEMON.new" && chmod 0644 "$DAEMON.new" && mv "$DAEMON.new" "$DAEMON"
  # live-restore keeps running containers up across this restart; log options apply to containers created afterwards.
  systemctl enable docker >/dev/null 2>&1 || true
  systemctl restart docker
}

# ---- 4. the deploy user -------------------------------------------------------------------------------------------------------
st_user() { id "$DEPLOY_USER" >/dev/null 2>&1 && [[ " $(id -nG "$DEPLOY_USER") " == *" docker "* ]] && [[ "$(passwd -S "$DEPLOY_USER" 2>/dev/null | awk '{print $2}')" =~ ^(L|LK)$ ]]; }
do_user() {
  id "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"
  getent group docker >/dev/null || groupadd docker
  usermod -aG docker "$DEPLOY_USER"
  passwd -l "$DEPLOY_USER" >/dev/null   # no password at all: key login only
}
SUDOERS="/etc/sudoers.d/90-abb-$DEPLOY_USER"
st_sudoers() { [ "$(cat "$SUDOERS" 2>/dev/null)" = "$DEPLOY_USER ALL=(ALL) NOPASSWD:ALL" ] && visudo -cf "$SUDOERS" >/dev/null 2>&1; }
do_sudoers() {
  # root cannot log in over ssh after stage 2, so this user is how an operator becomes root. A user in the docker group can already
  # start a root container, so passwordless sudo adds no new power; it is stated here, not hidden.
  local tmp; tmp="$(mktemp)"; printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$DEPLOY_USER" >"$tmp"
  visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "generated sudoers file is invalid"; }
  install -m 0440 -o root -g root "$tmp" "$SUDOERS"; rm -f "$tmp"
}
valid_keys() { # public keys from KEY_FILE, one per line, validated; a private key is refused outright
  [ -n "$KEY_FILE" ] || return 1
  grep -q 'PRIVATE KEY' "$KEY_FILE" && die "$KEY_FILE contains a private key: pass the PUBLIC key (.pub)" 2
  local line n=0 tmp; tmp="$(mktemp)"
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    printf '%s\n' "$line" >"$tmp"
    ssh-keygen -l -f "$tmp" >/dev/null 2>&1 || { rm -f "$tmp"; die "not a valid ssh public key line in $KEY_FILE" 2; }
    printf '%s\n' "$line"; n=$((n + 1))
  done <"$KEY_FILE"
  rm -f "$tmp"; [ "$n" -gt 0 ] || die "no public key in $KEY_FILE" 2
}
AUTH() { printf '%s/.ssh/authorized_keys' "$(home_of)"; }
st_keys() {
  [ -f "$(AUTH)" ] || return 1
  if [ -z "$KEY_FILE" ]; then [ -s "$(AUTH)" ]; return; fi
  local k; while IFS= read -r k; do grep -qxF "$k" "$(AUTH)" || return 1; done < <(valid_keys)
}
do_keys() {
  [ -n "$KEY_FILE" ] || die "no public key given: pass --ssh-key-file KEY.pub (without a key the server cannot be reached after stage 2)" 2
  local h; h="$(home_of)"
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$h/.ssh"
  touch "$(AUTH)"; chmod 0600 "$(AUTH)"; chown "$DEPLOY_USER:$DEPLOY_USER" "$(AUTH)"
  local k; while IFS= read -r k; do grep -qxF "$k" "$(AUTH)" || printf '%s\n' "$k" >>"$(AUTH)"; done < <(valid_keys)
}

# ---- 5. directories ------------------------------------------------------------------------------------------------------------
dir_ok() { [ -d "$1" ] && [ "$(stat -c '%a %U' "$1")" = "$2 $DEPLOY_USER" ]; }
st_dirs() {
  dir_ok /opt/abb 755 && dir_ok /opt/abb/backups 700 && dir_ok /opt/abb/secrets 700 && dir_ok /opt/edge 755 \
    && dir_ok /etc/abb 700 && dir_ok /var/lib/node_exporter/textfile 755
}
do_dirs() {
  install -d -m 0755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /opt/abb /opt/edge /var/lib/node_exporter /var/lib/node_exporter/textfile
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /opt/abb/backups /opt/abb/secrets /etc/abb
}

# ---- 6. swap + sysctl ------------------------------------------------------------------------------------------------------------
st_swap() { [ "$SWAP_MB" = 0 ] && return 0; [ -n "$(swapon --show --noheadings 2>/dev/null)" ] && grep -q '^/swapfile ' /etc/fstab && [ "$(cat /etc/sysctl.d/90-abb-swap.conf 2>/dev/null)" = "vm.swappiness=10" ]; }
do_swap() {
  if [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then
    [ -e /swapfile ] || { fallocate -l "${SWAP_MB}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_MB" status=none; }
    chmod 0600 /swapfile; mkswap /swapfile >/dev/null; swapon /swapfile
  fi
  grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >>/etc/fstab
  printf 'vm.swappiness=10\n' >/etc/sysctl.d/90-abb-swap.conf
  sysctl -q -p /etc/sysctl.d/90-abb-swap.conf >/dev/null 2>&1 || true
}

# ---- 7. firewall ---------------------------------------------------------------------------------------------------------------
ssh_ports() { # every port sshd listens on, plus the port of the session running this script: never close the door we came in by
  { sshd -T 2>/dev/null | awk '$1=="port"{print $2}'; [ -n "${SSH_CONNECTION:-}" ] && printf '%s\n' "$(printf '%s' "$SSH_CONNECTION" | awk '{print $4}')"; echo 22; } | sort -un
}
ufw_rules_ok() {
  local out; out="$(ufw status verbose 2>/dev/null)" || return 1
  grep -q '^Status: active' <<<"$out" || return 1
  grep -q '^Default: deny (incoming), allow (outgoing)' <<<"$out" || return 1
  local p; for p in $(ssh_ports); do grep -Eq "^$p/tcp +(LIMIT|ALLOW) IN" <<<"$out" || return 1; done
  grep -Eq '^80/tcp +ALLOW IN' <<<"$out" && grep -Eq '^443/tcp +ALLOW IN' <<<"$out" || return 1
  if [ "$HTTP3" = 1 ]; then grep -Eq '^443/udp +ALLOW IN' <<<"$out" || return 1; fi
}
st_ufw() { ufw_rules_ok; }
do_ufw() {
  ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
  local p; for p in $(ssh_ports); do ufw limit "$p/tcp" >/dev/null; done   # allowed BEFORE enabling: the order is the lockout protection
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  if [ "$HTTP3" = 1 ]; then ufw allow 443/udp >/dev/null; fi
  ufw --force enable >/dev/null
}

# ---- 8. fail2ban ---------------------------------------------------------------------------------------------------------------
JAIL=/etc/fail2ban/jail.d/abb-sshd.local
want_jail() { printf '# Managed by scripts/hosting-bootstrap.sh\n[sshd]\nenabled = true\nbackend = systemd\nmaxretry = 5\nfindtime = 10m\nbantime = 1h\n'; }
st_fail2ban() { [ "$(cat "$JAIL" 2>/dev/null)" = "$(want_jail)" ]; }
do_fail2ban() { install -d -m 0755 /etc/fail2ban/jail.d; want_jail >"$JAIL"; systemctl enable fail2ban >/dev/null 2>&1 || true; systemctl restart fail2ban; }

# ---- 9. unattended security upgrades ----------------------------------------------------------------------------------------
UU=/etc/apt/apt.conf.d/52abb-unattended
PERIODIC=/etc/apt/apt.conf.d/20auto-upgrades
want_uu() {
  printf '// Managed by scripts/hosting-bootstrap.sh. Security updates only; Docker and the app are upgraded deliberately.\n'
  # shellcheck disable=SC2016 # ${distro_id} is apt's variable, written literally
  printf 'Unattended-Upgrade::Allowed-Origins {\n  "${distro_id}:${distro_codename}-security";\n  "${distro_id}ESMApps:${distro_codename}-apps-security";\n  "${distro_id}ESM:${distro_codename}-infra-security";\n};\n'
  printf 'Unattended-Upgrade::Remove-Unused-Dependencies "true";\n'
  if [ "$AUTO_REBOOT" = 1 ]; then printf 'Unattended-Upgrade::Automatic-Reboot "true";\nUnattended-Upgrade::Automatic-Reboot-Time "04:30";\nUnattended-Upgrade::Automatic-Reboot-WithUsers "false";\n'
  else printf 'Unattended-Upgrade::Automatic-Reboot "false";\n'; fi
}
want_periodic() { printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\nAPT::Periodic::AutocleanInterval "7";\n'; }
st_uu() { [ "$(cat "$UU" 2>/dev/null)" = "$(want_uu)" ] && [ "$(cat "$PERIODIC" 2>/dev/null)" = "$(want_periodic)" ]; }
do_uu() { want_uu >"$UU"; want_periodic >"$PERIODIC"; chmod 0644 "$UU" "$PERIODIC"; systemctl enable unattended-upgrades >/dev/null 2>&1 || true; systemctl restart unattended-upgrades 2>/dev/null || true; }

# ---- 10. time, network, units ---------------------------------------------------------------------------------------------------
st_time() { [ "$(timedatectl show -p NTP --value 2>/dev/null)" = yes ]; }
do_time() { timedatectl set-ntp true; }
st_network() { docker network inspect edge >/dev/null 2>&1; }
do_network() { docker network create edge >/dev/null; }
UNITS=(abb-backup.service abb-backup.timer)
st_units() { local u; for u in "${UNITS[@]}"; do cmp -s "$REPO/deploy/hosting/systemd/$u" "/etc/systemd/system/$u" || return 1; done; }
do_units() { local u; for u in "${UNITS[@]}"; do install -m 0644 "$REPO/deploy/hosting/systemd/$u" "/etc/systemd/system/$u"; done; systemctl daemon-reload; }

# ---- stage 2: harden ssh ---------------------------------------------------------------------------------------------------------
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-abb-hardening.conf
want_sshd() {
  printf '# Managed by scripts/hosting-bootstrap.sh --harden-ssh. Named 00- because the first value sshd reads wins\n'
  printf '# (cloud-init ships 50-cloud-init.conf with PasswordAuthentication yes).\n'
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nPubkeyAuthentication yes\n'
  printf 'PermitEmptyPasswords no\nMaxAuthTries 4\nLoginGraceTime 30\nX11Forwarding no\n'
}
sshd_effective_ok() {
  local t; t="$(sshd -T 2>/dev/null)" || return 1
  grep -qx 'passwordauthentication no' <<<"$t" && grep -qx 'permitrootlogin no' <<<"$t" \
    && grep -qx 'pubkeyauthentication yes' <<<"$t" && grep -qx 'kbdinteractiveauthentication no' <<<"$t"
}
# Evidence that the key works: a publickey login of the deploy user in the log and a deploy session that is open right now.
key_login_logged() {
  local logs
  logs="$({ journalctl -u ssh -u ssh.socket --no-pager -o cat --since "-${EVIDENCE_HOURS}h" 2>/dev/null || true; cat /var/log/auth.log 2>/dev/null || true; })"
  grep -Eq "Accepted publickey for $DEPLOY_USER from " <<<"$logs"
}
deploy_session_open() { local users; users="$(who 2>/dev/null | awk '{print $1}' || true)"; grep -qx "$DEPLOY_USER" <<<"$users"; }
harden_ssh() {
  [ -s "$(AUTH)" ] || die "REFUSED: $DEPLOY_USER has no authorized_keys; run --apply --ssh-key-file first" 3
  st_sudoers || die "REFUSED: $DEPLOY_USER has no working sudo; root login would be the only way in" 3
  key_login_logged || die "REFUSED: no 'Accepted publickey for $DEPLOY_USER' login in the last ${EVIDENCE_HOURS}h. Open a SECOND terminal, ssh in as $DEPLOY_USER with your key, leave it open, then run this again." 3
  deploy_session_open || die "REFUSED: no $DEPLOY_USER session is open right now. Keep the key-login terminal open while this runs." 3
  if sshd_effective_ok && [ "$(cat "$SSHD_DROPIN" 2>/dev/null)" = "$(want_sshd)" ]; then say "OK     ssh is already key-only"; return 0; fi
  local backup=""; [ -f "$SSHD_DROPIN" ] && { backup="$(mktemp)"; cp "$SSHD_DROPIN" "$backup"; }
  rollback() { if [ -n "$backup" ]; then cp "$backup" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi; systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true; }
  say "DO     write $SSHD_DROPIN"
  want_sshd >"$SSHD_DROPIN"; chmod 0644 "$SSHD_DROPIN"
  if ! sshd -t; then rollback; die "sshd -t rejected the new configuration; nothing was reloaded and the old file is restored"; fi
  if ! sshd_effective_ok; then rollback; die "the effective sshd configuration is not key-only (another file wins?); restored"; fi
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
  sleep 1
  local p; for p in $(ssh_ports); do
    if ! ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$p\$"; then rollback; die "sshd is not listening on $p after the reload; restored"; fi
  done
  say "OK     ssh is key-only; this session and the $DEPLOY_USER session stay open. Test a fresh login as $DEPLOY_USER before closing either."
}

# ---- run ---------------------------------------------------------------------------------------------------------------------------
if [ "$MODE" = ssh ]; then harden_ssh; exit $?; fi

if [ "$MODE" = apply ] && ! st_keys && [ -z "$KEY_FILE" ]; then
  die "pass --ssh-key-file KEY.pub (your PUBLIC key): without it nobody could log in once ssh is hardened" 2
fi
if [ -n "$KEY_FILE" ]; then
  [ -r "$KEY_FILE" ] || die "cannot read $KEY_FILE" 2
  valid_keys >/dev/null   # dies here, in the main shell, on a private key or an invalid line
fi

say "== host bootstrap ($MODE) for Ubuntu ${VERSION_ID}, deploy user '$DEPLOY_USER', Docker series ${DOCKER_SERIES} =="
step "install packages: ${PACKAGES[*]}" st_packages do_packages
step "add Docker's apt repository (key fingerprint checked) and pin docker-ce to ${DOCKER_SERIES}" st_docker_repo do_docker_repo
step "install Docker Engine, buildx and the compose plugin" st_docker do_docker
step "write /etc/docker/daemon.json (json-file 10m x 3, live-restore) and restart docker" st_daemon do_daemon
step "create user $DEPLOY_USER (no password, docker group)" st_user do_user
step "grant $DEPLOY_USER passwordless sudo (root-equivalent via docker anyway)" st_sudoers do_sudoers
step "install the ssh public key(s) for $DEPLOY_USER" st_keys do_keys
step "create /opt/abb, /opt/abb/backups, /opt/abb/secrets, /opt/edge, /etc/abb, the node_exporter textfile directory" st_dirs do_dirs
step "create a ${SWAP_MB} MiB swapfile and set vm.swappiness=10" st_swap do_swap
step "firewall: ufw deny incoming; allow ssh (rate-limited), 80/tcp, 443/tcp$([ "$HTTP3" = 1 ] && echo ', 443/udp')" st_ufw do_ufw
step "fail2ban sshd jail (5 failures in 10 min -> 1 h ban)" st_fail2ban do_fail2ban
step "unattended security upgrades$([ "$AUTO_REBOOT" = 1 ] && echo ', reboot at 04:30 only when required')" st_uu do_uu
step "enable NTP time synchronisation" st_time do_time
step "create the Docker network 'edge'" st_network do_network
step "install the abb-backup systemd units (not enabled)" st_units do_units

if [ "$MODE" = check ]; then
  say "BOOTSTRAP-CHECK: $pending action(s) pending; nothing was changed"
else
  say "BOOTSTRAP-APPLY: done ($pending action(s) applied). SSH is NOT hardened yet."
  say "Next: from a second terminal run  ssh -i <your key> $DEPLOY_USER@<server>  and keep it open, then run  sudo $0 --harden-ssh"
  say "Reminder: Docker publishes container ports around ufw. Only Caddy may publish (80/443); check with scripts/hosting-verify.sh --server."
fi
