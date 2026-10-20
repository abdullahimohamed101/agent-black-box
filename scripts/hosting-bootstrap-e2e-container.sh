#!/usr/bin/env bash
# The part of scripts/hosting-bootstrap-e2e.sh that runs INSIDE the throwaway Ubuntu 24.04 container (repository mounted at /repo).
# shellcheck disable=SC2015,SC2086 # A && B || fail is the shape of a test; `set -- $d` splits a pair on purpose
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HOSTING-BOOTSTRAP-E2E FAILED: $*" >&2; exit 1; }
B=/repo/scripts/hosting-bootstrap.sh
V="python3 /repo/scripts/hosting_verify_lib.py"
# Output is captured before grep: `cmd | grep -q` under pipefail can fail by SIGPIPE when grep leaves early.
says() { $V --server >/tmp/says.out 2>&1 || true; grep -q "$1" /tmp/says.out; }

step "prerequisites of the harness (an Ubuntu 24.04 cloud image already has these)"
apt-get update -qq >/dev/null
apt-get install -y -qq openssh-client python3-minimal iproute2 util-linux >/dev/null 2>&1
ssh-keygen -q -t ed25519 -N '' -f /tmp/id_test && ssh-keygen -q -t ed25519 -N '' -f /tmp/id_other

step "stubs for what a container cannot do (in /usr/local/bin, ahead of the real tools, also for dpkg scripts)"
cat >/usr/local/bin/systemctl <<'S'
#!/bin/bash
echo "systemctl $*" >>/tmp/systemctl.log
case "$*" in
  "reload ssh"|"reload sshd") [ -f /run/sshd.pid ] && kill -HUP "$(cat /run/sshd.pid)"; exit 0 ;;
  "is-enabled unattended-upgrades"|"is-enabled abb-backup.timer") [ -f /etc/apt/apt.conf.d/52abb-unattended ] && { echo enabled; exit 0; }; echo disabled; exit 1 ;;
  "is-active abb-backup.timer") echo inactive; exit 3 ;;
esac
exit 0
S
cat >/usr/local/bin/ufw <<'S'
#!/bin/bash
R=/tmp/ufw.rules; A=/tmp/ufw.active; D=/tmp/ufw.default
touch "$R"; [ -f "$D" ] || echo allow >"$D"
case "$1" in
  default) [ "$2" = deny ] && [ "$3" = incoming ] && echo deny >"$D"; exit 0 ;;
  limit|allow) echo "$1 $2" >>"$R"; exit 0 ;;
  --force) [ "$2" = enable ] && : >"$A"; exit 0 ;;
  status)
    [ -f "$A" ] || { echo "Status: inactive"; exit 0; }
    echo "Status: active"; echo "Logging: on (low)"; echo "Default: $(cat "$D") (incoming), allow (outgoing), disabled (routed)"; echo "New profiles: skip"; echo
    printf '%-26s %-11s %s\n' To Action From; printf '%-26s %-11s %s\n' -- ------ ----
    while read -r verb spec; do a=ALLOW; [ "$verb" = limit ] && a=LIMIT; printf '%-26s %-11s %s\n' "$spec" "$a IN" Anywhere; done <"$R"
    while read -r verb spec; do a=ALLOW; [ "$verb" = limit ] && a=LIMIT; printf '%-26s %-11s %s\n' "$spec (v6)" "$a IN" "Anywhere (v6)"; done <"$R" ;;
esac
S
cat >/usr/local/bin/timedatectl <<'S'
#!/bin/bash
[ "$1" = set-ntp ] && { echo yes >/tmp/ntp; exit 0; }
case "$*" in
  "show -p NTP --value") cat /tmp/ntp 2>/dev/null || echo no ;;
  "show -p NTPSynchronized --value") cat /tmp/ntp 2>/dev/null || echo no ;;
esac
S
cat >/usr/local/bin/swapon <<'S'
#!/bin/bash
if [ "${1:-}" = "--show" ]; then [ -f /tmp/swap.on ] && echo "/swapfile file 16M 0B -2"; exit 0; fi
: >/tmp/swap.on
S
cat >/usr/local/bin/docker <<'S'
#!/bin/bash
case "$1 $2" in
  "network inspect") [ -f /tmp/docker.net ] && exit 0; exit 1 ;;
  "network create") echo "$3" >/tmp/docker.net; echo fakeid; exit 0 ;;
  "info --format") echo json-file; exit 0 ;;
esac
[ "$1" = ps ] && { cat /tmp/docker.ps 2>/dev/null; exit 0; }
exec /usr/bin/docker "$@"
S
cat >/usr/local/bin/fail2ban-client <<'S'
#!/bin/bash
[ -f /etc/fail2ban/jail.d/abb-sshd.local ] && { echo "Status for the jail: sshd"; exit 0; }; echo "Sorry but the jail 'sshd' does not exist" >&2; exit 255
S
cat >/usr/local/bin/who <<'S'
#!/bin/bash
cat /tmp/who.txt 2>/dev/null || true
S
cat >/usr/local/bin/journalctl <<'S'
#!/bin/bash
cat /tmp/journal.txt 2>/dev/null || true
S
chmod +x /usr/local/bin/*

step "A9.0 BEFORE: the --server checks on an unconfigured host must FAIL where they can run here"
$V --server --abb-dir /opt/abb --textfile-dir /var/lib/node_exporter/textfile >/tmp/before.out 2>&1 || true
sed 's/^/  /' /tmp/before.out
for c in firewall.ufw upgrades.unattended secrets.modes backup.fresh fail2ban.sshd swap.present time.synced; do
  grep -q "^FAIL $c" /tmp/before.out || fail "$c should FAIL on an unconfigured host"
done

step "A9.1 --check lists every action and changes NOTHING"
fingerprint() { (find /etc /opt /home /root /usr/local /var/lib/dpkg/status /swapfile -xdev -type f -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null; id deploy 2>&1 || true) | sha256sum; }
before="$(fingerprint)"
$B --check --ssh-key-file /tmp/id_test.pub >/tmp/check1.out
sed 's/^/  /' /tmp/check1.out
steps="$(grep -c '^step "' /repo/scripts/hosting-bootstrap.sh)"
[ "$(grep -c '^WOULD ' /tmp/check1.out)" = "$steps" ] || fail "expected $steps WOULD lines, got $(grep -c '^WOULD ' /tmp/check1.out)"
grep -q "BOOTSTRAP-CHECK: $steps action(s) pending; nothing was changed" /tmp/check1.out || fail "check summary"
[ "$(fingerprint)" = "$before" ] || fail "--check changed the system"
$B --dry-run >/tmp/dry.out; tail -1 /tmp/dry.out | grep -q 'nothing was changed' || fail "--dry-run alias"
echo "PASS A9.1 ($steps actions listed, filesystem fingerprint identical)"

step "A9.2 refusals: not root, no key, a private key, a bad key line, wrong OS is not testable here"
setpriv --reuid=65534 --regid=65534 --clear-groups $B --apply --ssh-key-file /tmp/id_test.pub >/tmp/r1.out 2>&1 && fail "ran as non-root"
grep -q 'run as root' /tmp/r1.out || fail "non-root message"
$B --apply >/tmp/r2.out 2>&1 && fail "applied without a key"; grep -q 'pass --ssh-key-file' /tmp/r2.out || fail "no-key message"
$B --apply --ssh-key-file /tmp/id_test >/tmp/r3.out 2>&1 && fail "accepted a PRIVATE key"; grep -q 'contains a private key' /tmp/r3.out || fail "private-key message"
echo "ssh-ed25519 not-a-key garbage" >/tmp/bad.pub
$B --apply --ssh-key-file /tmp/bad.pub >/tmp/r4.out 2>&1 && fail "accepted an invalid key"; grep -q 'not a valid ssh public key' /tmp/r4.out || fail "bad-key message"
[ ! -d /home/deploy ] || fail "a refused run created the user"
echo "PASS A9.2"

step "A9.3 --apply (stage 1): packages, Docker from its repo pinned to 5:29.*, user, ufw, fail2ban, upgrades, units"
ABB_SWAP_MB=16 $B --apply --ssh-key-file /tmp/id_test.pub >/tmp/apply1.out 2>&1 || { tail -30 /tmp/apply1.out; fail "apply failed"; }
sed 's/^/  /' /tmp/apply1.out | grep -v '^  *$' | tail -25
id deploy | grep -q docker || fail "deploy is not in the docker group"
[ "$(passwd -S deploy | awk '{print $2}')" = L ] || fail "deploy has a usable password"
[ "$(stat -c %a /home/deploy/.ssh/authorized_keys)" = 600 ] && grep -qF "$(cut -d' ' -f1,2 /tmp/id_test.pub)" /home/deploy/.ssh/authorized_keys || fail "authorized_keys"
visudo -cf /etc/sudoers.d/90-abb-deploy >/dev/null || fail "sudoers"
python3 -c 'import json; d=json.load(open("/etc/docker/daemon.json")); assert d["log-driver"]=="json-file" and d["log-opts"]["max-size"]=="10m" and d["live-restore"] is True' || fail "daemon.json"
v="$(dpkg-query -W -f='${Version}' docker-ce)"; case "$v" in 5:29.*) ;; *) fail "docker-ce $v is outside the pinned series" ;; esac
apt-cache policy docker-ce | grep -q 'Pinned\|1001' || apt-cache policy docker-ce | sed -n 1,4p
grep -q '1001' <(apt-cache policy docker-ce) || fail "the apt pin (priority 1001) is not in effect"
echo "docker-ce $v ; $(/usr/bin/docker compose version --short 2>&1 | head -1) (compose plugin)"
python3 - "$(/usr/bin/docker compose version --short)" <<'PY' || fail "compose older than 2.24"
import sys; sys.exit(0 if [int(x) for x in sys.argv[1].lstrip("v").split(".")[:2]] >= [2, 24] else 1)
PY
for d in "/opt/abb 755" "/opt/abb/backups 700" "/opt/abb/secrets 700" "/etc/abb 700"; do set -- $d; [ "$(stat -c '%a %U' $1)" = "$2 deploy" ] || fail "$1 mode/owner"; done
cmp /repo/deploy/hosting/systemd/abb-backup.timer /etc/systemd/system/abb-backup.timer || fail "unit not installed"
grep -q '^limit 22/tcp' /tmp/ufw.rules && grep -q '^allow 80/tcp' /tmp/ufw.rules && grep -q '^allow 443/tcp' /tmp/ufw.rules || fail "ufw rules"
grep -q 'Automatic-Reboot "true"' /etc/apt/apt.conf.d/52abb-unattended || fail "unattended config"
grep -q '^/swapfile ' /etc/fstab && [ "$(stat -c %a /swapfile)" = 600 ] || fail "swap"
echo "PASS A9.3"

step "A9.4 idempotent: a second --apply and a --check change nothing and report zero actions"
before="$(fingerprint)"
ABB_SWAP_MB=16 $B --apply --ssh-key-file /tmp/id_test.pub >/tmp/apply2.out 2>&1 || fail "second apply failed"
grep -q 'BOOTSTRAP-APPLY: done (0 action(s) applied)' /tmp/apply2.out || { cat /tmp/apply2.out; fail "second apply did something"; }
[ "$(fingerprint)" = "$before" ] || fail "the second apply changed files"
ABB_SWAP_MB=16 $B --check >/tmp/chk2.out; tail -1 /tmp/chk2.out | grep -q '0 action(s) pending' || fail "check after apply is not clean"
echo "PASS A9.4"

step "A9.5 stage 2 (--harden-ssh) is LOCKOUT-SAFE: it refuses without proof and changes nothing"
ssh-keygen -A >/dev/null; mkdir -p /run/sshd; /usr/sbin/sshd -o PidFile=/run/sshd.pid
sleep 1; ss -ltn | grep -q ':22 ' || fail "test sshd is not listening"
pw() { sshd -T | awk '$1=="passwordauthentication"{print $2}'; }
[ "$(pw)" = yes ] || fail "precondition: password auth should be on"
refused() { local why="$1"; shift; if "$@" >/tmp/h.out 2>&1; then fail "stage 2 ran: $why"; fi; grep -q 'REFUSED\|key-only\|not key-only' /tmp/h.out || { cat /tmp/h.out; fail "unexpected message: $why"; }; }
refused "no log evidence" $B --harden-ssh;               echo "  no key login logged   -> $(grep -m1 REFUSED /tmp/h.out | cut -c1-110)"
echo "Oct 19 12:00:00 host sshd[1]: Accepted publickey for deploy from 203.0.113.9 port 51234 ssh2: ED25519 SHA256:abc" >/tmp/journal.txt
refused "no open session" $B --harden-ssh;              echo "  login logged, no session -> $(grep -m1 REFUSED /tmp/h.out | cut -c1-110)"
echo "deploy   pts/0        2026-10-19 12:00 (203.0.113.9)" >/tmp/who.txt
mv /home/deploy/.ssh/authorized_keys /tmp/ak.keep
refused "no authorized_keys" $B --harden-ssh;           echo "  no authorized_keys       -> $(grep -m1 REFUSED /tmp/h.out | cut -c1-110)"
mv /tmp/ak.keep /home/deploy/.ssh/authorized_keys
mv /etc/sudoers.d/90-abb-deploy /tmp/sudo.keep
refused "no sudo" $B --harden-ssh;                      echo "  no sudo for deploy       -> $(grep -m1 REFUSED /tmp/h.out | cut -c1-110)"
mv /tmp/sudo.keep /etc/sudoers.d/90-abb-deploy
[ ! -e /etc/ssh/sshd_config.d/00-abb-hardening.conf ] && [ "$(pw)" = yes ] || fail "a refused run changed sshd"
# another file that sorts first and keeps passwords on: the effective config is not key-only, so it must roll back
printf 'PasswordAuthentication yes\n' >/etc/ssh/sshd_config.d/00-aaa-conflict.conf
refused "a conflicting drop-in wins" $B --harden-ssh;   echo "  conflicting drop-in      -> $(grep -m1 'not key-only' /tmp/h.out | cut -c1-110)"
[ ! -e /etc/ssh/sshd_config.d/00-abb-hardening.conf ] && [ "$(pw)" = yes ] || fail "the failed hardening was not rolled back"
rm /etc/ssh/sshd_config.d/00-aaa-conflict.conf
ss -ltn | grep -q ':22 ' || fail "sshd stopped listening during the refused runs"
echo "PASS A9.5"

step "A9.6 stage 2 with the evidence: key-only, sshd still listening, idempotent"
$B --harden-ssh | tee /tmp/h2.out | sed 's/^/  /'
[ "$(pw)" = no ] || fail "password authentication is still on"
sshd -T >/tmp/sshdT.out; grep -qx 'permitrootlogin no' /tmp/sshdT.out || fail "root login is still on"
sleep 1; ss -ltn | grep -q ':22 ' || fail "sshd is not listening after the reload"
grep -q 'reload ssh' /tmp/systemctl.log || fail "sshd was not reloaded"
$B --harden-ssh >/tmp/h3.out; grep -q 'already key-only' /tmp/h3.out || fail "second stage 2 is not a no-op"
echo "PASS A9.6"

step "A9.7 hosting-secrets.sh in the container: owner, modes, exclusive create, nothing printed"
S=/repo/scripts/hosting-secrets.sh
$S --origin https://abb.example.org --acme-email ops@example.org >/tmp/s1.out 2>&1 || { cat /tmp/s1.out; fail "secrets script"; }
sed 's/^/  /' /tmp/s1.out
for f in /opt/abb/.env /opt/abb/secrets/metrics_token /opt/abb/secrets/grafana_admin_password /etc/abb/backup.pass /etc/abb/backup.env; do
  [ "$(stat -c '%a %U' $f)" = "600 deploy" ] || fail "$f is $(stat -c '%a %U' $f)"
done
if grep -qE '[0-9a-f]{32}|[A-Za-z0-9+/]{40,}' /tmp/s1.out; then fail "the script printed something that looks like a secret"; fi
for k in ABB_POSTGRES_PASSWORD ABB_RUNTIME_DB_PASSWORD ABB_RETENTION_DB_PASSWORD ABB_METRICS_TOKEN; do
  v="$(sed -n "s/^$k=//p" /opt/abb/.env)"; [ "${#v}" -ge 32 ] || fail "$k was not generated"
done
[ "$(sed -n 's/^ABB_METRICS_TOKEN=//p' /opt/abb/.env)" = "$(cat /opt/abb/secrets/metrics_token)" ] || fail "the two metrics token copies differ"
grep -q '^ABB_OIDC_CLIENT_SECRET=$' /opt/abb/.env || fail "the OIDC secret must stay empty for the owner"
sha256sum /opt/abb/.env /etc/abb/backup.pass >/tmp/sec.sum
$S --origin https://abb.example.org --acme-email other@example.org >/tmp/s2.out 2>&1
[ "$(grep -c '^KEPT' /tmp/s2.out)" = 5 ] || fail "a second run did not keep all five files"
sha256sum -c /tmp/sec.sum >/dev/null || fail "a second run overwrote a secret"
$S --origin http://abb.example.org --acme-email ops@example.org >/dev/null 2>&1 && fail "accepted an http origin"
$S --origin https://abb.example.org/ --acme-email ops@example.org >/dev/null 2>&1 && fail "accepted a trailing slash"
echo "PASS A9.7"

step "A9.8 AFTER: the same --server checks now PASS where they can run here, and the planted problems turn them red again"
printf 'abb_hosting_backup_last_success_timestamp_seconds %s\nabb_hosting_backup_offsite_last_success_timestamp_seconds %s\n' "$(date +%s)" "$(date +%s)" >/var/lib/node_exporter/textfile/abb_backup.prom
echo "edge-caddy-1|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" >/tmp/docker.ps
$V --server >/tmp/after.out 2>&1 || true
sed 's/^/  /' /tmp/after.out
for c in firewall.ufw ssh.key_only ports.listening docker.published_ports upgrades.unattended disk.root docker.log_driver docker.compose_version secrets.modes backup.fresh fail2ban.sshd swap.present time.synced; do
  grep -q "^PASS $c" /tmp/after.out || fail "$c should PASS on the configured host"
done
# planted problems against the REAL system
chmod 644 /opt/abb/.env; says '^FAIL secrets.modes' || fail "a 0644 .env was not caught"; chmod 600 /opt/abb/.env
python3 -m http.server 5433 --bind 0.0.0.0 >/dev/null 2>&1 & hs=$!; sleep 1
says '^FAIL ports.listening.*5433' || fail "a listener on 0.0.0.0:5433 was not caught"; kill $hs
echo "serverflow-gateway-1|0.0.0.0:18080->8080/tcp" >>/tmp/docker.ps
says '^FAIL docker.published_ports' || fail "a published port was not caught"
rm /etc/ssh/sshd_config.d/00-abb-hardening.conf; kill -HUP "$(cat /run/sshd.pid)"; sleep 1
says '^FAIL ssh.key_only' || fail "re-enabled password login was not caught"
echo "PASS A9.8"
printf '\nHOSTING-BOOTSTRAP-E2E PASSED\n'
