"""Builds the demo working copy: the intentionally broken repository, a Git history and a local remote."""

import os
import shutil
import subprocess
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "repo_template"
LOG_LINES = 3200  # about 220 KB: large enough that the UI must load it in chunks

# Planted on purpose. The demo proves none of these survive into storage or the UI.
PLANTED = {
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "bearer_token": "Bearer 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
    "db_url_password": "hunter2-prod-password",
    "private_key_body": "MIIEowIBAAKCAQEAplantedplantedplantedplanted",
}


def planted_literals() -> list[str]:
    """Exact strings that must never be found in stored data (E2E asserts this)."""
    values = [
        PLANTED["aws_access_key"],
        PLANTED["bearer_token"].split()[1],
        PLANTED["db_url_password"],
        PLANTED["private_key_body"],
        "rk_" + "live_51Hq9dKd8s7Fh2LmPzQw4TxYv",  # the refresh token in the test fixtures
    ]
    env_secret = os.environ.get("ABB_DEMO_ENV_SECRET")
    if env_secret:
        values.append(env_secret)
    return values


def _log_text() -> str:
    lines = []
    for i in range(LOG_LINES):
        lines.append(
            f"2026-10-07T09:{i // 60 % 60:02d}:{i % 60:02d}Z INFO auth-server session=s{i % 97} refresh ok in {i % 40} ms"
        )
        if i == 700:
            lines.append(
                f"2026-10-07T09:11:40Z DEBUG idp request headers: Authorization: {PLANTED['bearer_token']}"
            )
        if i == 1400:
            lines.append(
                f"2026-10-07T09:23:20Z WARN env dump AWS_ACCESS_KEY_ID={PLANTED['aws_access_key']}"
            )
            lines.append(
                f"2026-10-07T09:23:20Z WARN datasource postgres://app:{PLANTED['db_url_password']}@db.internal:5432/app"
            )
        if i == 1900:
            lines.append("2026-10-07T09:31:40Z ERROR signing key loaded:")
            lines += [
                "-----BEGIN RSA PRIVATE KEY-----",
                PLANTED["private_key_body"],
                "-----END RSA PRIVATE KEY-----",
            ]
        if i == 2900:
            lines.append(
                f"2026-10-07T09:36:40Z DEBUG service token {os.environ.get('ABB_DEMO_ENV_SECRET', 'not-set-in-this-run')}"
            )
        if i == 3000:
            lines.append(
                "2026-10-07T09:38:20Z ERROR refresh dropped the refresh token for session s7 (provider omitted refresh_token)"
            )
    return "\n".join(lines) + "\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def prepare_workspace(dest: Path) -> Path:
    """Copy the template to `dest/repo`, commit it, and give it a bare local `origin` (offline push)."""
    root, remote = dest / "repo", dest / "origin.git"
    if root.exists() or remote.exists():
        raise FileExistsError(f"{dest} already holds a demo workspace")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "logs").mkdir()
    (root / "logs" / "auth-server.log").write_text(_log_text())
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    _git(root, "init", "-b", "main")
    for key, value in (
        ("user.name", "Demo Agent"), ("user.email", "agent@example.invalid"),
        ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null"),
    ):  # fmt: skip
        _git(root, "config", key, value)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "Initial commit: session service")
    _git(root, "remote", "add", "origin", str(remote))
    return root
