"""Self-test of scripts/hosting_server_checks.py (Phase 23, step 8): `hosting-verify.sh --server`.

A well-configured host (realistic command outputs) passes every assertion; each planted defect (ufw off, an extra rule, password
login on, Postgres listening on 0.0.0.0, a container publishing 5433, unattended upgrades off, a full disk, journald logs
without rotation, a 0644 .env, a stale or missing backup, no fail2ban, no swap, unsynchronised clock, an old Compose) turns the
matching assertion red, and only it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import hosting_server_checks as server  # noqa: E402

NOW = 1_760_875_200.0

UFW_GOOD = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     LIMIT IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
22/tcp (v6)                LIMIT IN    Anywhere (v6)
80/tcp (v6)                ALLOW IN    Anywhere (v6)
443/tcp (v6)               ALLOW IN    Anywhere (v6)
"""
SSHD_GOOD = "port 22\npasswordauthentication no\npermitrootlogin no\npubkeyauthentication yes\nkbdinteractiveauthentication no\npermitemptypasswords no\n"
SS_GOOD = """udp   UNCONN 0      0       127.0.0.53%lo:53         0.0.0.0:*
udp   UNCONN 0      0      10.0.0.2%eth0:68         0.0.0.0:*
tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*
tcp   LISTEN 0      4096         0.0.0.0:80         0.0.0.0:*
tcp   LISTEN 0      4096         0.0.0.0:443        0.0.0.0:*
tcp   LISTEN 0      4096            [::]:22            [::]:*
tcp   LISTEN 0      4096            [::]:443           [::]:*
tcp   LISTEN 0      4096       127.0.0.1:9090       0.0.0.0:*
"""
DOCKER_PS_GOOD = ("edge-caddy-1|0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp, 2019/tcp\n"
                  "agent-black-box-api-1|8000/tcp\nagent-black-box-prometheus-1|127.0.0.1:9090->9090/tcp\n")
APT_GOOD = 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\nUnattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-security";\n'
DAEMON_GOOD = '{"log-driver": "json-file", "log-opts": {"max-file": "3", "max-size": "10m"}, "live-restore": true}'
PROM_GOOD = ("abb_hosting_backup_last_success_timestamp_seconds %d\nabb_hosting_backup_offsite_last_success_timestamp_seconds %d\n"
             % (NOW - 3600, NOW - 3500))


class FakeHost(server.Host):
    def __init__(self, **over) -> None:
        self.commands: Dict[Tuple[str, ...], Tuple[int, str]] = {
            ("ufw", "status", "verbose"): (0, UFW_GOOD),
            ("sshd", "-T"): (0, SSHD_GOOD),
            ("ss", "-H", "-lntu"): (0, SS_GOOD),
            ("docker", "ps", "--format", "{{.Names}}|{{.Ports}}"): (0, DOCKER_PS_GOOD),
            ("apt-config", "dump"): (0, APT_GOOD),
            ("systemctl", "is-enabled", "unattended-upgrades"): (0, "enabled\n"),
            ("docker", "info", "--format", "{{.LoggingDriver}}"): (0, "json-file\n"),
            ("docker", "compose", "version", "--short"): (0, "5.6.0\n"),
            ("systemctl", "is-active", "abb-backup.timer"): (0, "active\n"),
            ("systemctl", "is-enabled", "abb-backup.timer"): (0, "enabled\n"),
            ("fail2ban-client", "status", "sshd"): (0, "Status for the jail: sshd\n|- Filter\n"),
            ("swapon", "--show", "--noheadings"): (0, "/swapfile file 2G 0B -2\n"),
            ("timedatectl", "show", "-p", "NTPSynchronized", "--value"): (0, "yes\n"),
        }
        self.files: Dict[str, Optional[str]] = {"/etc/docker/daemon.json": DAEMON_GOOD, "/var/lib/node_exporter/textfile/abb_backup.prom": PROM_GOOD}
        self.modes: Dict[str, Tuple[int, str]] = {
            "/opt/abb/.env": (0o600, "deploy"), "/etc/abb/backup.pass": (0o600, "deploy"),
            "/opt/abb/secrets": (0o700, "deploy"), "/opt/abb/backups": (0o700, "deploy"),
        }
        self.disks: Dict[str, Tuple[float, float]] = {"/": (31.0, 4.0), "/var/lib/docker": (31.0, 4.0)}
        self.mtimes: Dict[str, float] = {}
        for key, value in over.items():
            getattr(self, key).update(value)

    def run(self, argv: Sequence[str], timeout: int = 30) -> Tuple[int, str]:
        return self.commands.get(tuple(argv), (127, ""))

    root = True

    def is_root(self) -> bool:
        return self.root

    def read(self, path: str) -> Optional[str]:
        return self.files.get(path)

    def mode_owner(self, path: str) -> Optional[Tuple[int, str]]:
        return self.modes.get(path)

    def disk(self, path: str) -> Optional[Tuple[float, float]]:
        return self.disks.get(path)

    def mtime(self, path: str) -> Optional[float]:
        return self.mtimes.get(path)

    def now(self) -> float:
        return NOW


def failing(host: FakeHost, **settings) -> List[str]:
    return sorted(r.check for r in server.run_server_checks(host, server.Settings(**settings)) if r.status == "FAIL")


def statuses(host: FakeHost) -> Dict[str, str]:
    return {r.check: r.status for r in server.run_server_checks(host, server.Settings())}


class ServerChecks(unittest.TestCase):
    def test_a_well_configured_host_passes_everything(self):
        results = server.run_server_checks(FakeHost(), server.Settings())
        self.assertEqual([r.line() for r in results if r.status != "PASS"], [])
        self.assertGreaterEqual(len(results), 17)

    def planted(self, check, **over):
        host = FakeHost(**over)
        self.assertEqual(failing(host), [check], msg=[r.line() for r in server.run_server_checks(host, server.Settings())])

    def test_ufw_inactive(self):
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, "Status: inactive\n")})

    def test_ufw_default_allow(self):
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, UFW_GOOD.replace("deny (incoming)", "allow (incoming)"))})

    def test_ufw_extra_rule(self):
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, UFW_GOOD + "5433/tcp                   ALLOW IN    Anywhere\n")})

    def test_ufw_rule_without_a_protocol_opens_both(self):
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, UFW_GOOD + "8000                       ALLOW IN    Anywhere\n")})

    def test_ufw_missing_ssh_rule_and_missing_https(self):
        no_ssh = UFW_GOOD.replace("22/tcp                     LIMIT IN    Anywhere\n", "").replace("22/tcp (v6)                LIMIT IN    Anywhere (v6)\n", "")
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, no_ssh)})
        no_443 = "\n".join(l for l in UFW_GOOD.splitlines() if not l.startswith("443/tcp")) + "\n"
        self.planted("firewall.ufw", commands={("ufw", "status", "verbose"): (0, no_443)})

    def test_http3_rule_is_extra_unless_enabled(self):
        text = UFW_GOOD + "443/udp                    ALLOW IN    Anywhere\n"
        host = FakeHost(commands={("ufw", "status", "verbose"): (0, text)})
        self.assertEqual(failing(host), ["firewall.ufw"])
        self.assertEqual(failing(host, http3=True), [])

    def test_ssh_password_login_or_root_login(self):
        self.planted("ssh.key_only", commands={("sshd", "-T"): (0, SSHD_GOOD.replace("passwordauthentication no", "passwordauthentication yes"))})
        self.planted("ssh.key_only", commands={("sshd", "-T"): (0, SSHD_GOOD.replace("permitrootlogin no", "permitrootlogin yes"))})
        self.planted("ssh.key_only", commands={("sshd", "-T"): (0, SSHD_GOOD.replace("kbdinteractiveauthentication no\n", ""))})

    def test_postgres_listening_on_all_addresses(self):
        self.planted("ports.listening", commands={("ss", "-H", "-lntu"): (0, SS_GOOD + "tcp   LISTEN 0      4096         0.0.0.0:5433       0.0.0.0:*\n")})
        self.planted("ports.listening", commands={("ss", "-H", "-lntu"): (0, SS_GOOD + "tcp   LISTEN 0      4096            [::]:8000          [::]:*\n")})
        self.planted("ports.listening", commands={("ss", "-H", "-lntu"): (0, SS_GOOD + "udp   UNCONN 0      0            0.0.0.0:443        0.0.0.0:*\n")})

    def test_loopback_listeners_are_fine(self):
        extra = "tcp   LISTEN 0      4096       127.0.0.1:3001       0.0.0.0:*\ntcp   LISTEN 0 4096 [::1]:8000 [::]:*\n"
        self.assertEqual(failing(FakeHost(commands={("ss", "-H", "-lntu"): (0, SS_GOOD + extra)})), [])

    def test_container_publishing_a_private_port(self):
        bad = DOCKER_PS_GOOD + "agent-black-box-postgres-1|0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp\n"
        self.planted("docker.published_ports", commands={("docker", "ps", "--format", "{{.Names}}|{{.Ports}}"): (0, bad)})
        also = DOCKER_PS_GOOD + "serverflow-gateway-1|0.0.0.0:18080->8080/tcp\n"
        self.planted("docker.published_ports", commands={("docker", "ps", "--format", "{{.Names}}|{{.Ports}}"): (0, also)})
        notcaddy = DOCKER_PS_GOOD + "something|0.0.0.0:443->443/tcp\n"
        self.planted("docker.published_ports", commands={("docker", "ps", "--format", "{{.Names}}|{{.Ports}}"): (0, notcaddy)})

    def test_unattended_upgrades_off(self):
        self.planted("upgrades.unattended", commands={("apt-config", "dump"): (0, APT_GOOD.replace('Unattended-Upgrade "1"', 'Unattended-Upgrade "0"'))})
        self.planted("upgrades.unattended", commands={("systemctl", "is-enabled", "unattended-upgrades"): (1, "disabled\n")})
        self.planted("upgrades.unattended", commands={("apt-config", "dump"): (0, APT_GOOD.replace("-security", "-updates"))})

    def test_disk_blocks_or_inodes_over_the_limit(self):
        self.planted("disk.root", disks={"/": (91.0, 4.0)})
        self.planted("disk.docker", disks={"/var/lib/docker": (30.0, 83.0)})

    def test_docker_logs_without_rotation(self):
        self.planted("docker.log_driver", files={"/etc/docker/daemon.json": "{}"})
        self.planted("docker.log_driver", commands={("docker", "info", "--format", "{{.LoggingDriver}}"): (0, "journald\n")})

    def test_old_compose(self):
        self.planted("docker.compose_version", commands={("docker", "compose", "version", "--short"): (0, "2.20.3\n")})
        self.assertEqual(failing(FakeHost(commands={("docker", "compose", "version", "--short"): (0, "v2.24.0\n")})), [])

    def test_secret_files_with_loose_modes(self):
        self.planted("secrets.modes", modes={"/opt/abb/.env": (0o644, "deploy")})
        self.planted("secrets.modes", modes={"/etc/abb/backup.pass": (0o640, "deploy")})
        self.planted("secrets.modes", modes={"/opt/abb/secrets": (0o755, "deploy")})
        self.planted("secrets.modes", modes={"/opt/abb/.env": (0o600, "nobody")})
        host = FakeHost()
        del host.modes["/opt/abb/.env"]
        self.assertEqual(failing(host), ["secrets.modes"])

    def test_backup_timer_missing_and_never_ran_is_a_failure_ki_243(self):
        host = FakeHost(commands={("systemctl", "is-active", "abb-backup.timer"): (3, "inactive\n"), ("systemctl", "is-enabled", "abb-backup.timer"): (1, "disabled\n")})
        self.assertEqual(failing(host), ["backup.timer"])
        host = FakeHost()
        del host.files["/var/lib/node_exporter/textfile/abb_backup.prom"]
        self.assertEqual(failing(host), ["backup.fresh"])
        self.assertIn("no backup has ever completed", [r for r in server.run_server_checks(host, server.Settings()) if r.check == "backup.fresh"][0].detail)

    def test_backup_stale_or_without_offsite_copy(self):
        stale = "abb_hosting_backup_last_success_timestamp_seconds %d\nabb_hosting_backup_offsite_last_success_timestamp_seconds %d\n" % (NOW - 50 * 3600, NOW - 50 * 3600)
        host = FakeHost(files={"/var/lib/node_exporter/textfile/abb_backup.prom": stale})
        self.assertEqual(failing(host), ["backup.fresh", "backup.offsite"])
        local_only = "abb_hosting_backup_last_success_timestamp_seconds %d\n" % (NOW - 3600)
        self.assertEqual(failing(FakeHost(files={"/var/lib/node_exporter/textfile/abb_backup.prom": local_only})), ["backup.offsite"])
        self.assertEqual(failing(FakeHost(files={"/var/lib/node_exporter/textfile/abb_backup.prom": "# nothing\n"})), ["backup.fresh"])

    def test_fail2ban_swap_time_reboot(self):
        self.planted("fail2ban.sshd", commands={("fail2ban-client", "status", "sshd"): (255, "")})
        # not root: fail2ban-client cannot answer, which is a SKIP and never a red FAIL on a healthy host (review P2-6)
        user = FakeHost(commands={("fail2ban-client", "status", "sshd"): (255, "")})
        user.root = False
        self.assertEqual(failing(user), [])
        self.assertIn("SKIP", [r.status for r in server.check_fail2ban(user)])
        self.planted("swap.present", commands={("swapon", "--show", "--noheadings"): (0, "")})
        self.planted("time.synced", commands={("timedatectl", "show", "-p", "NTPSynchronized", "--value"): (0, "no\n")})
        self.planted("host.reboot_pending", mtimes={"/var/run/reboot-required": NOW - 9 * 86400})
        self.assertEqual(failing(FakeHost(mtimes={"/var/run/reboot-required": NOW - 86400})), [])

    def test_unreadable_facts_are_skipped_not_passed(self):
        host = FakeHost(commands={("ufw", "status", "verbose"): (1, ""), ("sshd", "-T"): (1, "")})
        results = {r.check: r.status for r in server.run_server_checks(host, server.Settings())}
        self.assertEqual((results["firewall.ufw"], results["ssh.key_only"]), ("SKIP", "SKIP"))

    def test_outside_probe(self):
        self.assertEqual({r.status for r in server.probe_from_outside("h", connect=lambda h, p, t: p in (80, 443))}, {"PASS"})
        results = server.probe_from_outside("h", connect=lambda h, p, t: p in (80, 443, 5433, 9090))
        failed = [r for r in results if r.status == "FAIL"]
        self.assertEqual([r.check for r in failed], ["outside.private_ports_closed"])
        self.assertIn("5433", failed[0].detail)
        down = server.probe_from_outside("h", connect=lambda h, p, t: False)
        self.assertEqual([r.check for r in down if r.status == "FAIL"], ["outside.port_80", "outside.port_443"])

    def test_no_secret_value_is_ever_read(self):
        class Spy(FakeHost):
            def read(self, path):
                assert path in ("/etc/docker/daemon.json", "/var/lib/node_exporter/textfile/abb_backup.prom"), path
                return super().read(path)
        server.run_server_checks(Spy(), server.Settings())


if __name__ == "__main__":
    unittest.main()
