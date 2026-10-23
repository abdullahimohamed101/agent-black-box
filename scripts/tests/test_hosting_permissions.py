"""Linux uid/mode rules of the hosting scripts (live bugs of the first Hetzner deployment, KI-313).

The Docker Desktop rigs cannot see these: it ignores uid and mode on bind mounts. So the rules are pinned here (static, with a
planted violation each) and by scripts/hosting-linux-perms-e2e.sh (a real Linux container that enforces them).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List

SCRIPTS = Path(__file__).resolve().parent.parent
BACKUP = (SCRIPTS / "hosting-backup.sh").read_text()
DRILL = (SCRIPTS / "hosting-restore-drill.sh").read_text()
BOOTSTRAP = (SCRIPTS / "hosting-bootstrap.sh").read_text()
CHECKS = (SCRIPTS / "hosting_server_checks.py").read_text()
ENV_SH = SCRIPTS / "hosting-env.sh"


def bootstrap_backups_modes(text: str) -> List[str]:
    """Every mode bootstrap gives /opt/abb/backups: the `install -d -m MODE` that lists it and the `dir_ok ... backups MODE` test."""
    modes = []
    for line in text.splitlines():
        m = re.search(r"install -d -m 0?(\d+)\b.*?/opt/abb/backups\b", line)
        if m:
            modes.append(m.group(1))
        m = re.search(r"dir_ok /opt/abb/backups (\d+)", line)
        if m:
            modes.append(m.group(1))
    return modes


def verifier_backups_mode(text: str) -> str:
    m = re.search(r'\{s\.abb_dir\}/backups", 0o(\d+)\)', text)
    return m.group(1) if m else ""


def create_step_problems(text: str) -> List[str]:
    """The `backup create` container runs as uid 10001. It must not need to walk the host's 0700 backups directory."""
    lines = [ln for ln in text.splitlines() if re.search(r"\bbackup create\b", ln) and "dc run" in ln]
    if len(lines) != 1:
        return [f"expected one create line, found {len(lines)}"]
    ln = lines[0]
    problems = []
    if re.search(r"--out\s+\"?/backups/", ln):
        problems.append("--out is under /backups: uid 10001 must traverse the 0700 mount root")
    if not re.search(r"-v \"[^\"]*\.staging[^\"]*:/staging\"|-v \"\$\{?STAGING_HOST\}?:/staging\"", ln):
        problems.append("the create container does not mount only the .staging directory")
    return problems


class ModeRules(unittest.TestCase):
    def test_bootstrap_and_verifier_agree_on_the_backups_directory(self):
        self.assertEqual(sorted(set(bootstrap_backups_modes(BOOTSTRAP))), ["700"])
        self.assertEqual(verifier_backups_mode(CHECKS), "700")

    def test_a_planted_0711_workaround_is_a_disagreement(self):
        planted = BOOTSTRAP.replace("install -d -m 0700 -o \"$DEPLOY_USER\" -g \"$DEPLOY_USER\" /opt/abb/backups", "install -d -m 0711 -o \"$DEPLOY_USER\" -g \"$DEPLOY_USER\" /opt/abb/backups")
        self.assertNotEqual(planted, BOOTSTRAP, "the bootstrap line changed shape: update this test")
        self.assertNotEqual(sorted(set(bootstrap_backups_modes(planted))), ["700"])
        self.assertNotEqual(verifier_backups_mode(CHECKS.replace('/backups", 0o700)', '/backups", 0o711)')), "700")

    def test_the_backup_create_container_never_walks_the_backups_root(self):
        self.assertEqual(create_step_problems(BACKUP), [])

    def test_the_old_create_line_is_caught(self):
        old = 'dc run --rm -T -e ABB_BACKUP_PASSPHRASE backup create --out "/backups/$STAGING/$NAME" --encrypt --passphrase-env ABB_BACKUP_PASSPHRASE >/dev/null'
        self.assertEqual(len(create_step_problems(old)), 2)

    def test_the_backup_script_restores_a_loosened_directory(self):
        # an install that was chmod 0711 by hand (the live workaround) is returned to 0700 on the next run
        self.assertRegex(BACKUP, r'chmod 0?700 "\$BACKUP_DIR"')


def run_bash(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={**os.environ, **env}, check=False)  # noqa: S603,S607


class RcloneConfig(unittest.TestCase):
    def test_both_scripts_use_the_shared_helper(self):
        for text in (BACKUP, DRILL):
            self.assertIn("hosting-env.sh", text)
            self.assertIn("abb_rclone_default", text)
        self.assertIn("abb_rclone_diagnose", BACKUP)

    def test_default_is_used_when_unset_and_the_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d, "rclone.conf")
            conf.write_text("[x]\ntype = local\n")
            src = f'. "{ENV_SH}"; unset RCLONE_CONFIG; abb_rclone_default; echo "${{RCLONE_CONFIG:-unset}}"'
            out = run_bash(src, {"ABB_RCLONE_DEFAULT_CONF": str(conf)})
            self.assertEqual(out.stdout.strip(), str(conf), out.stderr)
            # an explicit value (from backup.env or the unit) is never overridden
            out = run_bash(src.replace("unset RCLONE_CONFIG;", "export RCLONE_CONFIG=/explicit;"), {"ABB_RCLONE_DEFAULT_CONF": str(conf)})
            self.assertEqual(out.stdout.strip(), "/explicit")
            # no file: stay unset so rclone uses its own default
            out = run_bash(src, {"ABB_RCLONE_DEFAULT_CONF": str(Path(d, "nope"))})
            self.assertEqual(out.stdout.strip(), "unset")

    @unittest.skipIf(os.geteuid() == 0, "root can read everything: the unreadable case cannot be planted")
    def test_an_unreadable_config_is_diagnosed_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d, "rclone.conf")
            conf.write_text("[x]\ntype = local\n")
            conf.chmod(0o000)  # stands in for root:root 0600 seen by the service user
            try:
                out = run_bash(f'. "{ENV_SH}"; export RCLONE_CONFIG="{conf}"; abb_rclone_diagnose', {})
            finally:
                conf.chmod(0o600)
            self.assertEqual(out.returncode, 1)
            self.assertIn("not readable by", out.stdout + out.stderr)
            self.assertIn("sudo", out.stdout + out.stderr)
            self.assertIn("chown", out.stdout + out.stderr)
            ok = run_bash(f'. "{ENV_SH}"; export RCLONE_CONFIG="{conf}"; abb_rclone_diagnose', {})
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_a_missing_explicit_config_is_diagnosed(self):
        out = run_bash(f'. "{ENV_SH}"; export RCLONE_CONFIG=/nonexistent/rclone.conf; abb_rclone_diagnose', {})
        self.assertEqual(out.returncode, 1)
        self.assertIn("does not exist", out.stdout + out.stderr)

    def test_guide_and_runbook_do_not_tell_the_operator_to_use_sudo_for_rclone_config(self):
        root = SCRIPTS.parent
        for rel in ("docs/hosting-guide.md", "docs/runbooks/hosting-operations.md"):
            text = (root / rel).read_text()
            self.assertNotRegex(text, r"sudo\s+rclone\s+--config", rel)
            self.assertIn("chown deploy:deploy /etc/abb/rclone.conf", text, rel)


class AlertReceiverFile(unittest.TestCase):
    """The receiver is configured in an untracked file (a modified tracked file blocks hosting-upgrade.sh)."""

    root = SCRIPTS.parent
    tmpl = (root / "deploy/hosting/alertmanager/alertmanager.yml.tmpl").read_text()
    compose = (root / "deploy/hosting/compose.observability.yml").read_text()

    def test_each_marker_appears_once_in_the_template_and_is_spliced_by_obs_init(self):
        for marker in ("__NOTIFY_INTEGRATIONS__", "__DEADMAN_INTEGRATIONS__"):
            self.assertEqual(self.tmpl.count(marker), 1, marker)
            self.assertIn(marker, self.compose)

    def test_alertmanager_waits_for_the_validation(self):
        self.assertRegex(self.compose, r"alertmanager:\n(?:.*\n)*?    depends_on:\n      obs-check: \{ condition: service_completed_successfully \}")

    def test_the_docs_do_not_tell_the_owner_to_edit_the_tracked_template(self):
        for rel in ("docs/hosting-guide.md", "docs/runbooks/hosting-operations.md"):
            text = (self.root / rel).read_text()
            self.assertIn("notify.yml", text, rel)
            self.assertNotRegex(text, r"Edit the `notify` receiver in", rel)


if __name__ == "__main__":
    unittest.main()
