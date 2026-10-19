"""Key handling of scripts/demo.sh, with `curl` and `uv` replaced by stubs (no stack): run with
`python3 -m unittest discover -s scripts/tests -v`.

The stubs record their argv and stdin. What must hold: the key never appears in any argv, a caller-chosen key file's
existing parent directory keeps its permissions, a valid key is reused, and a missing or stale one is re-issued with --supersede.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN = "abb_live_" + "a" * 12 + "." + "S" * 43  # shaped like a key, assembled at run time

CURL = """#!/bin/sh
echo "curl $*" >>"$STUB_LOG"
case "$*" in *"-K -"*) cat >>"$STUB_STDIN"; [ "$STUB_KEY_VALID" = 1 ] || exit 22 ;; esac
exit 0
"""
UV = f"""#!/bin/sh
echo "uv $*" >>"$STUB_LOG"
case "$*" in *create-key*) echo "{TOKEN}"; exit 0 ;; esac
exit 1
"""


class DemoKeyHandling(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        for name, body in (("curl", CURL), ("uv", UV)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.bin = bin_dir
        self.shared = self.dir / "shared"  # a caller-owned directory that must not be chmod-ed
        self.shared.mkdir(mode=0o755)
        self.shared.chmod(0o755)
        self.log = self.dir / "log"
        self.stdin = self.dir / "stdin"
        self.log.touch()
        self.stdin.touch()

    def run_demo(self, key_file: Path, valid: bool) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "ABB_DATABASE_URL": "postgresql+asyncpg://x@localhost/x",
            "DEMO_KEY_FILE": str(key_file),
            "DEMO_STATE_DIR": str(self.dir / "state"),
            "STUB_LOG": str(self.log),
            "STUB_STDIN": str(self.stdin),
            "STUB_KEY_VALID": "1" if valid else "0",
        }
        # The stubbed `uv` fails the first example run, which ends the script after the key step: that is all this checks.
        return subprocess.run(
            ["bash", str(ROOT / "scripts/demo.sh")], env=env, text=True, capture_output=True, timeout=60, check=False
        )

    def test_new_key_is_private_superseding_and_never_in_argv(self) -> None:
        key_file = self.shared / "demo-key"
        self.run_demo(key_file, valid=False)
        self.assertEqual(key_file.read_text().strip(), TOKEN)
        self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.shared.stat().st_mode), 0o755, "a caller-owned directory was chmod-ed")
        log = self.log.read_text()
        self.assertIn("--supersede", log)
        self.assertNotIn(TOKEN, log)

    def test_key_validation_sends_the_key_on_stdin_not_argv(self) -> None:
        key_file = self.shared / "demo-key"
        key_file.write_text(TOKEN + "\n")
        self.run_demo(key_file, valid=True)
        self.assertIn(TOKEN, self.stdin.read_text())
        self.assertNotIn(TOKEN, self.log.read_text())

    def test_valid_key_is_reused(self) -> None:
        key_file = self.shared / "demo-key"
        key_file.write_text(TOKEN + "\n")
        self.run_demo(key_file, valid=True)
        self.assertNotIn("create-key", self.log.read_text())

    def test_stale_key_is_replaced_with_supersede(self) -> None:
        key_file = self.shared / "demo-key"
        key_file.write_text("abb_live_" + "b" * 12 + "." + "T" * 43 + "\n")
        self.run_demo(key_file, valid=False)
        self.assertEqual(key_file.read_text().strip(), TOKEN)
        self.assertIn("create-key", self.log.read_text())

    def test_a_directory_the_script_creates_is_private(self) -> None:
        key_file = self.dir / "fresh" / "demo-key"
        self.run_demo(key_file, valid=False)
        self.assertEqual(stat.S_IMODE(key_file.parent.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
