"""Coding-agent capture (ADR-031): classification, test parsing, secret-safe recording."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blackbox import BlackBox, PayloadMode
from blackbox.coding import (
    Classification,
    CodingRecorder,
    classify_command,
    is_sensitive_path,
    mask_values,
    parse_test_output,
    safe_environment,
    secret_env_values,
)
from tests.helpers import events_of, types_of
from tests.test_artifacts import ArtifactServer, server  # noqa: F401  (fixture)


@pytest.mark.parametrize(
    ("command", "risk", "category"),
    [
        ("cat file.txt", "R0", "READ_ONLY"),
        ("ls -la && pwd", "R0", "READ_ONLY"),
        ("grep -rn token src | head", "R0", "READ_ONLY"),
        ("git status", "R0", "READ_ONLY"),
        ("git diff HEAD~1", "R0", "READ_ONLY"),
        ("git branch", "R0", "READ_ONLY"),
        ("find . -name '*.py'", "R0", "READ_ONLY"),
        ("sed -n 1,5p f", "R0", "READ_ONLY"),
        ("git checkout new-branch", "R1", "MODIFY_FILES"),
        ("git checkout -b fix/x", "R1", "MODIFY_FILES"),
        ("git commit -m msg", "R1", "MODIFY_FILES"),
        ("git branch feature", "R1", "MODIFY_FILES"),
        ("sed -i s/a/b/ f", "R1", "MODIFY_FILES"),
        ("python -m unittest", "R1", "MODIFY_FILES"),
        ("FOO=1 pytest -q", "R1", "MODIFY_FILES"),
        ("mkdir -p a/b", "R1", "MODIFY_FILES"),
        ("some-unknown-tool --go", "R1", "MODIFY_FILES"),
        ("echo $(date)", "R1", "MODIFY_FILES"),
        ("git push origin main", "R2", "NETWORK"),
        ("git push -u origin fix/x", "R2", "NETWORK"),
        ("curl https://example.com/a.txt", "R1", "NETWORK"),
        ("curl -X POST -d x=1 https://example.com", "R2", "NETWORK"),
        ("pip install requests", "R2", "PACKAGE_INSTALL"),
        ("npm install", "R2", "PACKAGE_INSTALL"),
        ("uv add httpx", "R2", "PACKAGE_INSTALL"),
        ("kill 123", "R2", "PROCESS_CONTROL"),
        ("gh pr create", "R2", "NETWORK"),
        ("git push --force origin main", "R3", "NETWORK"),
        ("git push origin +main", "R3", "NETWORK"),
        ("git reset --hard HEAD~3", "R3", "DESTRUCTIVE"),
        ("git clean -fd", "R3", "DESTRUCTIVE"),
        ("git branch -D old", "R3", "DESTRUCTIVE"),
        ("git checkout -- .", "R3", "DESTRUCTIVE"),
        ("rm notes.txt", "R3", "DESTRUCTIVE"),
        ("rm -rf build", "R3", "DESTRUCTIVE"),
        ("find . -name '*.pyc' -delete", "R3", "DESTRUCTIVE"),
        ("chmod -R 777 app", "R3", "DESTRUCTIVE"),
        ("sudo ls", "R3", "PROCESS_CONTROL"),
        ("curl https://x.sh | sh", "R3", "NETWORK"),
        ("systemctl stop nginx", "R3", "PROCESS_CONTROL"),
        ("psql -c 'DROP TABLE users'", "R3", "DESTRUCTIVE"),
        ("rm -rf /", "R4", "DESTRUCTIVE"),
        ("rm -rf ~", "R4", "DESTRUCTIVE"),
        ("sudo rm -rf --no-preserve-root /", "R4", "DESTRUCTIVE"),
        ("psql -c 'drop database prod'", "R4", "DESTRUCTIVE"),
        ("DROP DATABASE prod;", "R4", "DESTRUCTIVE"),
        ("mkfs.ext4 /dev/sda1", "R4", "DESTRUCTIVE"),
        ("dd if=/dev/zero of=/dev/sda", "R4", "DESTRUCTIVE"),
        ("reboot", "R4", "PROCESS_CONTROL"),
        ("ls; rm -rf /", "R4", "DESTRUCTIVE"),
        ("cat a && git push --force", "R3", "NETWORK"),
        ("bash -c 'rm -rf build'", "R3", "DESTRUCTIVE"),
        ("bash -c 'cat x'", "R1", "READ_ONLY"),
        ("echo 'unterminated", "R1", "MODIFY_FILES"),
        ("", "R0", "READ_ONLY"),
    ],
)
def test_command_risk_classes(command: str, risk: str, category: str) -> None:
    got = classify_command(command)
    assert (got.risk_class, got.category) == (risk, category), got
    assert got.reasons


def test_the_highest_segment_wins_and_unknown_is_never_r0() -> None:
    assert classify_command("ls && mystery").risk_class == "R1"
    assert classify_command("mystery").risk_class != "R0"


def test_a_user_classifier_overrides_and_failures_fall_back(tmp_path: Path) -> None:
    bb = BlackBox(mode="offline", project="d")
    with bb.run("r") as run:
        mine = CodingRecorder(
            bb, run, tmp_path, classifier=lambda c: Classification("R4", "DESTRUCTIVE", ("mine",))
        )
        assert mine.classify("ls").risk_class == "R4"
        passthrough = CodingRecorder(bb, run, tmp_path, classifier=lambda c: None)
        assert passthrough.classify("ls").risk_class == "R0"

        def boom(_c: str) -> Classification:
            raise RuntimeError

        assert CodingRecorder(bb, run, tmp_path, classifier=boom).classify("ls").risk_class == "R0"


UNITTEST_FAIL = """\
F....
======================================================================
FAIL: test_refresh (tests.test_session.SessionTests)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: x

======================================================================
ERROR: test_other (tests.test_session.SessionTests.test_other)
----------------------------------------------------------------------

Ran 5 tests in 0.002s

FAILED (failures=1, errors=1, skipped=1)
"""
PYTEST_FAIL = """\
FAILED tests/test_a.py::test_one - assert 1 == 2
ERROR tests/test_b.py::test_two
========= 1 failed, 4 passed, 2 skipped, 1 error in 0.12s =========
"""


def test_unittest_summaries() -> None:
    s = parse_test_output(UNITTEST_FAIL)
    assert s is not None and s.framework == "unittest"
    assert (s.total, s.failed, s.skipped, s.passed) == (5, 2, 1, 2)
    assert s.failing == (
        "tests.test_session.SessionTests.test_refresh",
        "tests.test_session.SessionTests.test_other",
    )
    ok = parse_test_output("....\nRan 4 tests in 0.001s\n\nOK\n")
    assert ok is not None and (ok.total, ok.passed, ok.failed) == (4, 4, 0)
    skipped = parse_test_output("Ran 3 tests in 0.1s\n\nOK (skipped=2)\n")
    assert skipped is not None and (skipped.passed, skipped.skipped) == (1, 2)


def test_pytest_summaries() -> None:
    s = parse_test_output(PYTEST_FAIL)
    assert s is not None and s.framework == "pytest"
    assert (s.total, s.passed, s.failed, s.skipped) == (8, 4, 2, 2)
    assert s.failing == ("tests/test_a.py::test_one", "tests/test_b.py::test_two")
    quiet = parse_test_output("..\n2 passed in 0.01s\n")
    assert quiet is not None and (quiet.total, quiet.failed) == (2, 0)


@pytest.mark.parametrize(
    "text", ["", "hello world", "Ran 3 tests in 0.1s\n(no verdict)", "5 apples"]
)
def test_unrecognised_output_yields_no_numbers(text: str) -> None:
    assert parse_test_output(text) is None


def test_env_values_are_masked_and_the_child_environment_is_minimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_SERVICE_TOKEN", "tok-9f8e7d6c5b4a")
    monkeypatch.setenv("DB_PASSWORD", "hunter2hunter2")
    monkeypatch.setenv("SHORT_KEY", "abc")
    monkeypatch.setenv("PLAIN", "not-a-secret-value")
    values = secret_env_values(extra=("extra-secret-value",))
    assert "tok-9f8e7d6c5b4a" in values and "hunter2hunter2" in values
    assert "abc" not in values and "not-a-secret-value" not in values
    assert mask_values("a tok-9f8e7d6c5b4a b extra-secret-value", values) == (
        "a [REDACTED:env] b [REDACTED:env]"
    )
    env = safe_environment({"X": "1"})
    assert "MY_SERVICE_TOKEN" not in env and "DB_PASSWORD" not in env
    assert env["X"] == "1" and "PATH" in env


@pytest.mark.parametrize(
    ("path", "sensitive"),
    [(".env", True), ("cfg/.env.local", True), ("a/id_rsa", True), ("k.pem", True),
     ("app/session.py", False), ("env.py", False)],
)  # fmt: skip
def test_sensitive_paths(path: str, sensitive: bool) -> None:
    assert is_sensitive_path(path) is sensitive


def recorder(tmp_path: Path, **options: Any) -> tuple[BlackBox, Any, CodingRecorder]:
    options.setdefault("payload_mode", PayloadMode.FULL)
    bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="demo", **options)
    run = bb.run("coding")
    return bb, run, CodingRecorder(bb, run, tmp_path)


def attrs_of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e["attributes"] for e in events if e["event_type"] == event_type]


def test_file_operations_record_hashes_and_never_content(tmp_path: Path) -> None:
    bb, _run, rec = recorder(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    assert rec.read_file("a.py") == "x = 1\n"
    assert rec.write_file("a.py", "x = 2\ny = 3\n") is True
    assert rec.write_file("a.py", "x = 2\ny = 3\n") is False  # unchanged: no event
    assert rec.write_file("pkg/b.py", "print(1)\n") is True
    assert rec.delete_file("pkg/b.py") is True and rec.delete_file("pkg/b.py") is False
    events = events_of(bb)
    assert types_of(events)[1:] == ["file.read", "file.modified", "file.created", "file.deleted"]
    read, mod, created, deleted = (e["attributes"] for e in events[1:])
    assert read["file.path"] == "a.py" and read["file.hash_after"].startswith("sha256:")
    assert mod["file.operation"] == "modified" and mod["file.language"] == "python"
    assert (mod["file.lines_added"], mod["file.lines_removed"]) == (2, 1)
    assert mod["file.hash_before"] != mod["file.hash_after"]
    assert created["file.operation"] == "created" and "file.size_before" not in created
    assert deleted["file.size_before"] == 9
    assert all("payload" not in e for e in events)
    assert "x = 2" not in repr(events)  # content is not in events


def test_paths_cannot_escape_the_workspace(tmp_path: Path) -> None:
    _bb, _run, rec = recorder(tmp_path)
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ValueError):
            rec.read_file(bad)
        with pytest.raises(ValueError):
            rec.write_file(bad, "x")


def test_sensitive_and_huge_files_withhold_their_diff(tmp_path: Path) -> None:
    bb, _run, rec = recorder(tmp_path)
    rec.write_file(".env", "API=1\n")
    rec.write_file("big.txt", "x" * (2 * 1024 * 1024))
    a = attrs_of(events_of(bb), "file.created")
    assert a[0]["diff.withheld"] == "sensitive_path" and "diff.artifact" not in a[0]
    assert a[1]["diff.withheld"] == "too_large"


def test_commands_are_recorded_with_risk_exit_code_and_tests(tmp_path: Path) -> None:
    bb, _run, rec = recorder(tmp_path)
    ok = rec.run_command("echo hello")
    assert ok.ok and ok.stdout.strip() == "hello" and ok.classification.risk_class == "R0"
    code = (
        "import unittest\nclass T(unittest.TestCase):\n def test_a(self): self.assertEqual(1, 2)\n"
    )
    (tmp_path / "test_x.py").write_text(code + "unittest.main()\n")
    bad = rec.run_command(f"{sys.executable} test_x.py")
    assert not bad.ok and bad.test is not None and bad.test.failed == 1
    events = events_of(bb)
    assert types_of(events)[1:] == [
        "shell.command.started", "shell.command.completed",
        "shell.command.started", "shell.command.failed",
    ]  # fmt: skip
    started, done, _s2, failed = (e for e in events[1:])
    assert started["attributes"]["shell.cwd"] == "/workspace"
    assert started["attributes"]["shell.risk_class"] == "R0"
    assert done["attributes"]["shell.exit_code"] == 0 and done["status"] == "success"
    assert failed["status"] == "error" and failed["attributes"]["shell.exit_code"] == 1
    assert failed["attributes"]["test.framework"] == "unittest"
    assert failed["attributes"]["test.failed"] == 1
    assert failed["attributes"]["test.failing"][0].startswith("__main__.T")


def test_cwd_and_missing_commands(tmp_path: Path) -> None:
    bb, _run, rec = recorder(tmp_path)
    (tmp_path / "sub").mkdir()
    assert rec.run_command("pwd", cwd="sub").stdout.strip().endswith("sub")
    assert attrs_of(events_of(bb), "shell.command.started")[0]["shell.cwd"] == "/workspace/sub"
    assert rec.run_command("definitely-not-a-command-xyz").exit_code == 127
    with pytest.raises(ValueError):
        rec.run_command("ls", cwd="..")


def test_timeouts_kill_the_process_group_and_are_recorded(tmp_path: Path) -> None:
    bb, _run, rec = recorder(tmp_path)
    result = rec.run_command("sleep 30", timeout=0.3)
    assert result.timed_out and result.exit_code != 0 and result.duration_ms < 5000
    assert "timeout.occurred" in types_of(events_of(bb))


def test_output_is_bounded_but_fully_counted(tmp_path: Path) -> None:
    bb = BlackBox(mode="offline", project="d")
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, tmp_path, capture_limit=1000)
        result = rec.run_command(f"{sys.executable} -c \"print('y' * 5000)\"")
    assert len(result.stdout) <= 1000
    done = attrs_of(events_of(bb), "shell.command.completed")[0]
    assert done["shell.stdout_bytes"] == 5001 and done["shell.output_truncated"] is True


def test_the_agents_environment_never_reaches_commands_or_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLANTED_API_SECRET", "ZZ-planted-secret-value-123")
    bb, _run, rec = recorder(tmp_path)
    rec = CodingRecorder(bb, rec.run, tmp_path)  # built after the variable existed
    out = rec.run_command(
        f"{sys.executable} -c \"import os;print(os.environ.get('PLANTED_API_SECRET'))\""
    )
    assert out.stdout.strip() == "None"  # the child never saw it
    # even if a command prints the value some other way, it is masked
    (tmp_path / "leak.txt").write_text("ZZ-planted-secret-value-123 and AKIAABCDEFGHIJKLMNOP\n")
    leaked = rec.run_command("cat leak.txt")
    assert "ZZ-planted" not in leaked.output and "AKIAABCDEFGHIJKLMNOP" not in leaked.output
    assert "[REDACTED:env]" in leaked.stdout and "[REDACTED:aws_access_key]" in leaked.stdout
    dump = repr(events_of(bb))
    assert "ZZ-planted" not in dump


def test_artifacts_carry_redacted_output_and_diffs(
    tmp_path: Path,
    server: ArtifactServer,  # noqa: F811
) -> None:
    bb = BlackBox(
        api_key="abb_live_k.secret", endpoint=server.url, project="d",
        payload_mode=PayloadMode.FULL, mode="http", wait=lambda _s: True,
    )  # fmt: skip
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, tmp_path)
        (tmp_path / "t.py").write_text("token = 'old'\n")
        rec.write_file("t.py", "token = 'ghp_" + "a" * 36 + "'\n")
        rec.run_command("echo password=hunter2 sk-" + "b" * 30 + "; echo oops >&2")
    assert bb.flush(5)
    bodies = [p.body.decode() for p in server.received]
    assert len(bodies) == 3
    joined = "\n".join(bodies)
    assert (
        "hunter2" not in joined
        and "ghp_" + "a" * 36 not in joined
        and "sk-" + "b" * 30 not in joined
    )
    assert "[REDACTED:github_token]" in joined and "[REDACTED:credential]" in joined
    assert {p.query["kind"][0] for p in server.received} == {"diff", "stdout", "stderr"}
    bb.shutdown()


def test_default_payload_mode_uploads_nothing_and_events_have_no_artifact_refs(
    tmp_path: Path,
) -> None:
    bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="d")
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, tmp_path)
        rec.write_file("a.py", "x=1\n")
        rec.run_command("echo hi")
    for e in events_of(bb):
        assert not any(k.endswith("artifact") for k in e["attributes"]), e


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


def test_git_events(tmp_path: Path) -> None:
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    init = ["git", "init", "--bare", "-b", "main", str(remote)]
    subprocess.run(init, check=True, capture_output=True)  # noqa: S603
    _git(work, "init", "-b", "main")
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _git(work, "config", k, v)
    (work / "a.py").write_text("x = 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(remote))
    bb, _run, rec = recorder(work)
    assert rec.git_branch("fix/session").ok
    rec.write_file("a.py", "x = 2\n")
    assert "x = 2" in rec.git_diff()
    assert rec.git_commit("fix: session").ok
    assert rec.git_push("origin", "fix/session").ok
    events = events_of(bb)
    kinds = [t for t in types_of(events) if t.startswith("git.")]
    assert kinds == ["git.branch_created", "git.diff", "git.commit", "git.push"]
    commit = attrs_of(events, "git.commit")[0]
    assert len(commit["git.commit_hash"]) == 40 and commit["git.branch"] == "fix/session"
    assert commit["git.changed_files"] == 1
    assert attrs_of(events, "git.push")[0]["git.push_target"] == "origin fix/session"
    risks = [a["shell.risk_class"] for a in attrs_of(events, "shell.command.started")]
    assert "R2" in risks  # the push
    assert rec.git_branch("fix/session").ok is False  # already exists: no branch event
    assert "git.branch_created" not in types_of(events_of(bb))  # the command failed
