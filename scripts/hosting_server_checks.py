"""Host-level assertions for `scripts/hosting-verify.sh --server` (Phase 23, step 8). Standard library only, Python 3.9 compatible.

Run ON the server (as root, because `ufw status`, `sshd -T` and `fail2ban-client` need it; unreadable facts are SKIP, and SKIP
fails under --strict). Every check reads one fact through a `Host`, so the self-test (scripts/tests/test_hosting_server.py) feeds
good and planted-bad outputs and shows each assertion turn red. `probe_from_outside` runs from ANOTHER machine: connecting to the
server's own public address from itself never meets the firewall, so the closed-port assertion cannot be made on the host.

Nothing secret is read: only modes, owners, command status output and dates.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from hosting_verify_lib import Result, bad, ok, skip, verdict

ALLOWED_TCP = {22, 80, 443}
ALLOWED_UDP = {443, 68}  # 443 only when HTTP/3 is on (the ufw check says so); 68 is the DHCP client
PRIVATE_PORTS = (5432, 5433, 3000, 3001, 8000, 8900, 9090, 9093, 9100, 2019, 3100)
DISK_LIMIT = 80
MIN_COMPOSE = (2, 24)


class Host:
    """The facts of the machine. Replace the methods in a test."""

    def run(self, argv: Sequence[str], timeout: int = 30) -> Tuple[int, str]:
        if shutil.which(argv[0]) is None:
            return 127, ""
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except (OSError, subprocess.SubprocessError):
            return 126, ""
        return done.returncode, done.stdout

    def read(self, path: str) -> Optional[str]:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def mode_owner(self, path: str) -> Optional[Tuple[int, str]]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        try:
            import pwd

            owner = pwd.getpwuid(st.st_uid).pw_name
        except (ImportError, KeyError):
            owner = str(st.st_uid)
        return st.st_mode & 0o777, owner

    def disk(self, path: str) -> Optional[Tuple[float, float]]:
        """(percent of blocks used, percent of inodes used)."""
        try:
            s = os.statvfs(path)
        except OSError:
            return None
        blocks = 100.0 * (s.f_blocks - s.f_bfree) / s.f_blocks if s.f_blocks else 0.0
        inodes = 100.0 * (s.f_files - s.f_ffree) / s.f_files if s.f_files else 0.0
        return blocks, inodes

    def mtime(self, path: str) -> Optional[float]:
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def now(self) -> float:
        return time.time()


class Settings:
    def __init__(self, abb_dir: str = "/opt/abb", deploy_user: str = "deploy", textfile_dir: str = "/var/lib/node_exporter/textfile",
                 backup_pass: str = "/etc/abb/backup.pass", http3: bool = False, disk_limit: int = DISK_LIMIT,
                 backup_max_age_hours: float = 36.0, docker_root: str = "/var/lib/docker") -> None:
        self.abb_dir, self.deploy_user, self.textfile_dir = abb_dir, deploy_user, textfile_dir
        self.backup_pass, self.http3, self.disk_limit = backup_pass, http3, disk_limit
        self.backup_max_age_hours, self.docker_root = backup_max_age_hours, docker_root


def _needs_root(check: str, rc: int, what: str) -> Optional[Result]:
    if rc == 127:
        return bad(check, f"{what} is not installed")
    if rc != 0:
        return skip(check, f"{what} could not be read (run as root)")
    return None


# --------------------------------------------------------------------------- firewall


def parse_ufw(text: str) -> Tuple[bool, str, Set[str]]:
    """(active, default-incoming policy, allowed 'port/proto' set) from `ufw status verbose`."""
    active = bool(re.search(r"^Status: active", text, re.M))
    default = re.search(r"^Default: (\w+) \(incoming\)", text, re.M)
    allowed: Set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^(\d+(?::\d+)?)(?:/(tcp|udp))?(?: \(v6\))?\s+(ALLOW|LIMIT)\s+IN\b", line)
        if m:
            allowed.add(f"{m.group(1)}/{m.group(2) or 'any'}")
    return active, default.group(1) if default else "", allowed


def check_ufw(h: Host, s: Settings) -> List[Result]:
    rc, out = h.run(["ufw", "status", "verbose"])
    early = _needs_root("firewall.ufw", rc, "ufw")
    if early:
        return [early]
    active, default, allowed = parse_ufw(out)
    extra = sorted(a for a in allowed if a not in ("22/tcp", "80/tcp", "443/tcp") and not (a == "443/udp" and s.http3))
    problems = []
    if not active:
        problems.append("ufw is not active")
    if default != "deny":
        problems.append(f"default incoming policy is '{default or 'unknown'}', not deny")
    if extra:
        problems.append("rules beyond ssh/80/443: " + ", ".join(extra))
    for need in ("80/tcp", "443/tcp"):
        if need not in allowed:
            problems.append(f"{need} is not allowed (the site would be unreachable)")
    if "22/tcp" not in allowed:
        problems.append("22/tcp is not allowed: the next ufw reload may lock you out")
    return [verdict("firewall.ufw", not problems, "ufw active, default deny, only ssh/80/443 allowed", "; ".join(problems))]


# --------------------------------------------------------------------------- ssh


def check_sshd(h: Host) -> List[Result]:
    rc, out = h.run(["sshd", "-T"])
    early = _needs_root("ssh.key_only", rc, "sshd -T")
    if early:
        return [early]
    cfg = {}
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        cfg.setdefault(key.lower(), value.strip().lower())
    want = {"passwordauthentication": "no", "permitrootlogin": "no", "pubkeyauthentication": "yes",
            "kbdinteractiveauthentication": "no", "permitemptypasswords": "no"}
    wrong = [f"{k}={cfg.get(k, 'unset')} (want {v})" for k, v in want.items() if cfg.get(k) != v]
    return [verdict("ssh.key_only", not wrong, "password login off, root login off, key login on", "; ".join(wrong))]


# --------------------------------------------------------------------------- listeners


def parse_listeners(text: str) -> List[Tuple[str, str, int]]:
    """(proto, address, port) of every `ss -H -lntu` line (LISTEN or unconnected UDP)."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("tcp", "udp"):
            continue
        local = parts[4]
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        if port.isdigit():
            found.append((parts[0], addr, int(port)))
    return found


def check_listeners(h: Host, s: Settings) -> List[Result]:
    rc, out = h.run(["ss", "-H", "-lntu"])
    early = _needs_root("ports.listening", rc, "ss")
    if early:
        return [early]
    open_ports = []
    for proto, addr, port in parse_listeners(out):
        if addr in ("127.0.0.1", "::1") or addr.startswith("127."):
            continue
        allowed = ALLOWED_TCP if proto == "tcp" else ({443, 68} if s.http3 else {68})
        if port not in allowed:
            open_ports.append(f"{proto}/{port} on {addr or '*'}")
    return [verdict("ports.listening", not open_ports, "only 22, 80 and 443 listen on a non-loopback address",
                    "unexpected listener(s): " + ", ".join(sorted(set(open_ports))))]


def parse_docker_ports(text: str) -> List[Tuple[str, str, str, int]]:
    """(container, host address, proto, host port) of every published mapping in `docker ps --format '{{.Names}}|{{.Ports}}'`."""
    rows = []
    for line in text.splitlines():
        name, _, ports = line.partition("|")
        for chunk in ports.split(","):
            m = re.match(r"^\s*(?:(\[[^\]]+\]|[\d.]+|::):)?(\d+)(?:-(\d+))?->(\d+)(?:-\d+)?/(tcp|udp)", chunk)
            if m:
                rows.append((name.strip(), (m.group(1) or "0.0.0.0").strip("[]"), m.group(5), int(m.group(2))))
    return rows


def check_docker_published(h: Host, s: Settings) -> List[Result]:
    rc, out = h.run(["docker", "ps", "--format", "{{.Names}}|{{.Ports}}"])
    early = _needs_root("docker.published_ports", rc, "docker ps")
    if early:
        return [early]
    stray = []
    for name, addr, proto, port in parse_docker_ports(out):
        if addr in ("127.0.0.1", "::1"):
            continue
        if "caddy" in name and ((proto == "tcp" and port in (80, 443)) or (proto == "udp" and port == 443 and s.http3)):
            continue
        stray.append(f"{name} {proto}/{port} on {addr}")
    return [verdict("docker.published_ports", not stray, "only Caddy publishes 80/443; everything else is unpublished or loopback",
                    "published around ufw: " + ", ".join(sorted(set(stray))))]


# --------------------------------------------------------------------------- upgrades, disk, docker


def check_unattended(h: Host) -> List[Result]:
    rc, dump = h.run(["apt-config", "dump"])
    early = _needs_root("upgrades.unattended", rc, "apt-config")
    if early:
        return [early]
    enabled = re.search(r'^APT::Periodic::Unattended-Upgrade "1";', dump, re.M) is not None
    lists = re.search(r'^APT::Periodic::Update-Package-Lists "1";', dump, re.M) is not None
    security = "-security" in dump
    rc2, active = h.run(["systemctl", "is-enabled", "unattended-upgrades"])
    service = rc2 == 0 and active.strip() in ("enabled", "static", "alias")
    problems = []
    if not enabled:
        problems.append("APT::Periodic::Unattended-Upgrade is not 1")
    if not lists:
        problems.append("package lists are not refreshed daily")
    if not security:
        problems.append("no -security origin is allowed")
    if not service:
        problems.append("the unattended-upgrades service is not enabled")
    return [verdict("upgrades.unattended", not problems, "security updates are installed automatically", "; ".join(problems))]


def check_disk(h: Host, s: Settings) -> List[Result]:
    results = []
    for label, path in (("root", "/"), ("docker", s.docker_root)):
        use = h.disk(path)
        if use is None:
            results.append(skip(f"disk.{label}", f"{path} could not be read"))
            continue
        blocks, inodes = use
        results.append(verdict(f"disk.{label}", blocks < s.disk_limit and inodes < s.disk_limit,
                               f"{path}: {blocks:.0f}% of blocks, {inodes:.0f}% of inodes used (limit {s.disk_limit}%)",
                               f"{path}: {blocks:.0f}% of blocks, {inodes:.0f}% of inodes used (limit {s.disk_limit}%): "
                               "docker system df, docs/runbooks/hosting-operations.md"))
    return results


def check_docker_daemon(h: Host) -> List[Result]:
    rc, driver = h.run(["docker", "info", "--format", "{{.LoggingDriver}}"])
    early = _needs_root("docker.log_driver", rc, "docker info")
    if early:
        return [early]
    cfg = h.read("/etc/docker/daemon.json") or ""
    opts = re.search(r'"max-size"\s*:\s*"\d+[kmg]"', cfg) and re.search(r'"max-file"\s*:\s*"\d+"', cfg)
    good = driver.strip() == "json-file" and bool(opts)
    return [verdict("docker.log_driver", good, "json-file with max-size and max-file in daemon.json (logs cannot fill the disk)",
                    f"driver is '{driver.strip()}' and daemon.json {'has' if opts else 'has no'} max-size/max-file")]


def check_compose_version(h: Host) -> List[Result]:
    rc, out = h.run(["docker", "compose", "version", "--short"])
    early = _needs_root("docker.compose_version", rc, "docker compose")
    if early:
        return [early]
    m = re.match(r"v?(\d+)\.(\d+)", out.strip())
    good = bool(m) and (int(m.group(1)), int(m.group(2))) >= MIN_COMPOSE  # type: ignore[union-attr]
    return [verdict("docker.compose_version", good, f"Compose {out.strip()} (the overlay needs 2.24 or newer)",
                    f"Compose '{out.strip()}' is older than 2.24 (!reset and !override are not understood)")]


# --------------------------------------------------------------------------- files, backup


def check_file_modes(h: Host, s: Settings) -> List[Result]:
    want = [(f"{s.abb_dir}/.env", 0o600), (s.backup_pass, 0o600), (f"{s.abb_dir}/secrets", 0o700), (f"{s.abb_dir}/backups", 0o700)]
    wrong, missing = [], []
    for path, mode in want:
        info = h.mode_owner(path)
        if info is None:
            missing.append(path)
        elif info[0] != mode or info[1] not in (s.deploy_user, "root"):
            wrong.append(f"{path} is {info[0]:04o} owned by {info[1]} (want {mode:04o} {s.deploy_user})")
    if missing and not wrong:
        return [bad("secrets.modes", "missing: " + ", ".join(missing) + " (scripts/hosting-secrets.sh creates them)")]
    return [verdict("secrets.modes", not wrong and not missing, "secrets are 0600/0700 and owned by the deploy user", "; ".join(wrong + missing))]


def check_backup(h: Host, s: Settings) -> List[Result]:
    """KI-243: a backup that was never scheduled writes no file, so the absence of the file is itself the failure."""
    rc, active = h.run(["systemctl", "is-active", "abb-backup.timer"])
    rc2, enabled = h.run(["systemctl", "is-enabled", "abb-backup.timer"])
    results = [verdict("backup.timer", rc == 0 and active.strip() == "active" and rc2 == 0 and enabled.strip() == "enabled",
                       "abb-backup.timer is enabled and active",
                       f"abb-backup.timer is '{active.strip() or 'missing'}'/'{enabled.strip() or 'missing'}' (systemctl enable --now abb-backup.timer)")]
    text = h.read(f"{s.textfile_dir}/abb_backup.prom")
    if text is None:
        results.append(bad("backup.fresh", "no backup has ever completed (no metrics file): the first scheduled run is part of setup"))
        return results
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            k, _, v = line.partition(" ")
            try:
                values[k] = float(v)
            except ValueError:
                pass
    last = values.get("abb_hosting_backup_last_success_timestamp_seconds", 0.0)
    if not last:
        results.append(bad("backup.fresh", "the metrics file records no complete backup"))
        return results
    age = (h.now() - last) / 3600.0
    results.append(verdict("backup.fresh", -0.1 <= age <= s.backup_max_age_hours,
                           f"the last complete backup (created, verified, copied off-site) is {age:.1f} hours old",
                           f"the last complete backup is {age:.1f} hours old (limit {s.backup_max_age_hours:.0f})"))
    offsite = values.get("abb_hosting_backup_offsite_last_success_timestamp_seconds", 0.0)
    results.append(verdict("backup.offsite", offsite > 0 and (h.now() - offsite) / 3600.0 <= s.backup_max_age_hours,
                           "an off-site copy was made in the same window", "no recent off-site copy"))
    return results


# --------------------------------------------------------------------------- the rest of the host


def check_fail2ban(h: Host) -> List[Result]:
    rc, out = h.run(["fail2ban-client", "status", "sshd"])
    if rc == 127:
        return [bad("fail2ban.sshd", "fail2ban-client is not installed")]
    # fail2ban-client exits 255 both for a missing jail and for "permission denied" (not root): either way the jail is not proven.
    return [verdict("fail2ban.sshd", rc == 0 and "Status for the jail: sshd" in out, "the sshd jail is running",
                    "the sshd jail is not running or not readable (run as root)")]


def check_swap_time(h: Host) -> List[Result]:
    rc, out = h.run(["swapon", "--show", "--noheadings"])
    swap = ok("swap.present", out.split()[0]) if rc == 0 and out.strip() else bad("swap.present", "no swap (a 4 GB server needs the safety net)")
    rc2, ntp = h.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    synced = _needs_root("time.synced", rc2, "timedatectl") or verdict(
        "time.synced", ntp.strip() == "yes", "the clock is synchronised (certificates, logs and TOTP depend on it)", "the clock is not synchronised")
    reboot = h.mtime("/var/run/reboot-required")
    pending = (h.now() - reboot) / 86400.0 if reboot else None
    reboot_result = verdict("host.reboot_pending", pending is None or pending < 7,
                            "no reboot is waiting" if pending is None else f"a reboot has waited {pending:.1f} days",
                            f"a reboot has been required for {pending or 0:.0f} days (kernel updates are not active)")
    return [swap, synced, reboot_result]


CHECKS: Tuple[Callable[..., List[Result]], ...] = (
    check_ufw, check_sshd, check_listeners, check_docker_published, check_unattended, check_docker_daemon,
    check_compose_version, check_fail2ban, check_swap_time,
)


def run_server_checks(h: Optional[Host] = None, s: Optional[Settings] = None) -> List[Result]:
    h, s = h or Host(), s or Settings()
    results: List[Result] = []
    groups: List[Callable[[], List[Result]]] = [
        lambda: check_ufw(h, s), lambda: check_sshd(h), lambda: check_listeners(h, s), lambda: check_docker_published(h, s),
        lambda: check_unattended(h), lambda: check_disk(h, s), lambda: check_docker_daemon(h), lambda: check_compose_version(h),
        lambda: check_file_modes(h, s), lambda: check_backup(h, s), lambda: check_fail2ban(h), lambda: check_swap_time(h),
    ]
    for fn in groups:
        try:
            results.extend(fn())
        except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the others
            results.append(bad("server.error", f"{type(exc).__name__}: {str(exc)[:120]}"))
    return results


def probe_from_outside(host: str, timeout: float = 4.0, connect: Optional[Callable[[str, int, float], bool]] = None) -> List[Result]:
    """From another machine: the public ports answer and every private port does not (refused and silent both count as closed)."""

    def tcp(h: str, port: int, wait: float) -> bool:
        try:
            with socket.create_connection((h, port), timeout=wait):
                return True
        except OSError:
            return False

    can = connect or tcp
    results = []
    for port in (80, 443):
        results.append(verdict(f"outside.port_{port}", can(host, port, timeout), f"{host}:{port} answers", f"{host}:{port} does not answer"))
    open_private = [p for p in PRIVATE_PORTS if can(host, p, timeout)]
    results.append(verdict("outside.private_ports_closed", not open_private,
                           "none of " + ",".join(str(p) for p in PRIVATE_PORTS) + " answers from the internet",
                           "answering from the internet: " + ", ".join(str(p) for p in open_private)))
    return results
