"""scripts/hosting-compose.sh: the single source of the compose file list (review P1-3)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "hosting-compose.sh"
BASE = "docker-compose.yml deploy/hosting/compose.production.yml"
OBS = "deploy/hosting/compose.observability.yml"


def resolve(env_text: str | None, **env: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        envf = Path(d) / "env"
        if env_text is not None:
            envf.write_text(env_text)
        script = f'. "{LIB}"; abb_compose_resolve; echo "$compose_files"; echo "${{compose_profiles[*]}}"'
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True,
            env={"PATH": "/usr/bin:/bin", "ABB_COMPOSE_ENV_FILE": str(envf), **env},
        ).stdout.splitlines()
    return out[0], out[1]


class ComposeFiles(unittest.TestCase):
    def test_default_is_base_plus_production(self) -> None:
        self.assertEqual(resolve("ABB_PUBLIC_ORIGIN=https://x\n"), (BASE, "--profile ops"))

    def test_obs_uid_in_env_enables_the_overlay_and_profile(self) -> None:
        files, profiles = resolve("ABB_OBS_UID=1000\n")
        self.assertEqual(files, f"{BASE} {OBS}")
        self.assertEqual(profiles, "--profile ops --profile observability")

    def test_explicit_switch_wins(self) -> None:
        self.assertEqual(resolve("ABB_OBS_UID=1000\n", ABB_OBSERVABILITY="0")[0], BASE)
        self.assertEqual(resolve("", ABB_OBSERVABILITY="1")[0], f"{BASE} {OBS}")

    def test_explicit_list_and_extra(self) -> None:
        self.assertEqual(resolve("ABB_OBS_UID=1\n", ABB_COMPOSE_FILES="a.yml b.yml")[0], "a.yml b.yml")
        self.assertEqual(resolve(None, ABB_COMPOSE_EXTRA_FILES="rig.yml")[0], f"{BASE} rig.yml")


def lock(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB}"; {script}'], capture_output=True, text=True)


class Locks(unittest.TestCase):
    """Review P2-3: a lock left by a killed run must not block every later run."""

    def test_acquire_release_and_held_by_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lk = f"{d}/.lock"
            sleeper = subprocess.Popen(["sleep", "30"])
            try:
                Path(lk).mkdir()
                (Path(lk) / "pid").write_text(f"{sleeper.pid}\n")
                self.assertEqual(lock(f'abb_lock_acquire "{lk}"').returncode, 1, "a live owner keeps the lock")
            finally:
                sleeper.kill()
                sleeper.wait()
            self.assertEqual(lock(f'abb_lock_acquire "{lk}"').returncode, 0, "the owner died: stale, taken over")
            self.assertEqual(lock(f'abb_lock_acquire "{lk}"; abb_lock_release "{lk}"; test ! -e "{lk}"').returncode, 0)

    def test_lock_without_pid_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(f"{d}/.lock").mkdir()
            self.assertEqual(lock(f'abb_lock_acquire "{d}/.lock"').returncode, 1)


if __name__ == "__main__":
    unittest.main()
