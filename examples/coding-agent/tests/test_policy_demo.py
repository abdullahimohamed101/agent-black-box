"""The policy scenario (`run_demo.py --policy-demo`) against a fake policy server."""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_policy_server import FakePolicyServer

import run_demo

STARTER = Path(__file__).resolve().parent.parent / "policies" / "starter.yaml"


@pytest.fixture(autouse=True)
def python_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + ":" + __import__("os").environ["PATH"]
    )


def play(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], server: FakePolicyServer,
    *extra: str,
) -> tuple[int, list[dict[str, Any]]]:  # fmt: skip
    monkeypatch.setenv("BLACKBOX_API_KEY", "abb_live_demo.secret")
    monkeypatch.setenv("BLACKBOX_ENDPOINT", server.url)
    code = run_demo.main(["--policy-demo", *extra])
    lines = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    return code, lines


@pytest.fixture
def server() -> Iterator[FakePolicyServer]:
    fake = FakePolicyServer()
    yield fake
    fake.stop()


def test_blocked_read_then_a_push_that_waits_and_is_approved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], server: FakePolicyServer
) -> None:
    code, lines = play(monkeypatch, capsys, server, "--approval-timeout", "30")
    pending, summary = lines[0], lines[-1]
    assert code == 0
    assert pending["event"] == "approval_pending" and pending["approval_id"] == "apr_demo"
    assert (
        pending["policy_id"] == "approve-push-main" and 800 < pending["expires_in_seconds"] <= 902
    )
    assert summary["blocked"] == ["read_file"] and summary["pushed"] is True
    asked = [(i["capability"], i["resource"]) for i in server.decided]
    assert ("filesystem.read", ".env") in asked and ("github.push", "origin main") in asked
    assert [c["capability"] for c in server.consumed] == ["github.push"]  # the push spent it, once
    types = [e["event_type"] for e in server.events]
    assert "git.push" in types
    reads = [e["attributes"]["file.path"] for e in server.events if e["event_type"] == "file.read"]
    assert ".env" not in reads  # the denied read never happened


def test_the_denied_read_is_a_failed_tool_step_and_the_run_still_completes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], server: FakePolicyServer
) -> None:
    play(monkeypatch, capsys, server, "--approval-timeout", "30")
    failed = [e for e in server.events if e["event_type"] == "tool.call.failed"]
    assert [e["attributes"]["tool.name"] for e in failed] == ["read_file"]
    assert "run.completed" in [e["event_type"] for e in server.events]
    assert "run.failed" not in [e["event_type"] for e in server.events]


def test_a_denied_approval_leaves_the_push_undone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakePolicyServer(approval="deny")
    try:
        code, lines = play(monkeypatch, capsys, fake, "--approval-timeout", "30")
    finally:
        fake.stop()
    assert code == 0
    assert lines[-1]["blocked"] == ["read_file", "git_push"] and lines[-1]["pushed"] is False
    assert fake.consumed == []
    assert "git.push" not in [e["event_type"] for e in fake.events]


def test_an_approval_that_never_comes_ends_by_the_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakePolicyServer(approval="never")
    try:
        code, lines = play(monkeypatch, capsys, fake, "--approval-timeout", "0.6")
    finally:
        fake.stop()
    assert code == 0 and lines[-1]["pushed"] is False and "git_push" in lines[-1]["blocked"]
    assert fake.cancelled == ["/v1/approvals/apr_demo"]  # the waiting request was withdrawn


def test_the_flag_needs_a_server_and_the_scripted_model() -> None:
    for argv in (["--policy-demo", "--offline"], ["--policy-demo", "--variant", "wrong-fix"]):
        with pytest.raises(SystemExit):
            run_demo.main(argv)
    with pytest.raises(SystemExit):
        run_demo.main(["--approval-timeout", "5"])


def test_the_env_file_is_a_placeholder_and_is_never_committed(tmp_path: Path) -> None:
    from coding_agent.workspace import prepare_workspace

    root = prepare_workspace(tmp_path)
    run_demo.plant_env_file(root)
    assert "not a secret" in (root / ".env").read_text()
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".env" not in staged.split()


def test_the_starter_policy_is_valid_and_decides_the_scenario() -> None:
    parser = pytest.importorskip("abb_api.policies.parser")  # only in the API's environment
    evaluator = pytest.importorskip("abb_api.policies.evaluator")
    model = pytest.importorskip("abb_api.policies.model")
    result = parser.parse_policy(STARTER.read_text())
    assert result.ok, result.issues
    intent = model.Intent

    def decide(*args: Any) -> str:
        return str(evaluator.evaluate(result.policy, intent(*args)).decision.value)

    assert decide("filesystem.read", ".env") == "deny"
    assert decide("filesystem.read", "config/.env.local") == "deny"
    assert decide("filesystem.read", "app/session.py") == "allow"
    assert decide("github.push", "origin main", None, {"branch": "main"}) == "require_approval"
    assert decide("github.push", "origin x", None, {"branch": "production"}) == "require_approval"
    assert decide("github.push", "origin fix", None, {"branch": "fix/x"}) == "allow"
    assert decide("shell.execute", "rm", "R3", {"category": "DESTRUCTIVE"}) == "require_approval"
    assert decide("shell.execute", "mkfs", "R4", {}) == "deny"
    assert decide("shell.execute", "ls", "R0", {}) == "allow"
