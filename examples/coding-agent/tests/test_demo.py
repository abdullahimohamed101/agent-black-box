"""The scripted demo run, end to end, without a server (the browser E2E covers storage and the UI)."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blackbox import BlackBox, PayloadMode
from blackbox.coding import CodingRecorder

from coding_agent.agent import run_agent
from coding_agent.models import Reply, ScriptedModel, ToolCall
from coding_agent.tools import Toolbox, ToolError
from coding_agent.workspace import LOG_LINES, planted_literals, prepare_workspace


@pytest.fixture(autouse=True)
def python_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + ":" + __import__("os").environ["PATH"]
    )
    monkeypatch.setenv("ABB_DEMO_ENV_SECRET", "unknown-shape-secret-ZZ9")


def play(tmp_path: Path) -> tuple[list[dict[str, Any]], Any, Path]:
    root = prepare_workspace(tmp_path)
    bb = BlackBox(
        api_key="abb_live_k.secret", mode="offline", project="demo", payload_mode=PayloadMode.FULL
    )
    with bb.run("Fix OAuth session expiry") as run:
        result = run_agent(bb, run, CodingRecorder(bb, run, root), ScriptedModel())
    events = sorted(bb.buffered_events(), key=lambda e: e["sequence"])
    return events, result, root


def test_the_story_is_read_model_edit_fail_retry_edit_pass(tmp_path: Path) -> None:
    events, result, _root = play(tmp_path)
    assert (
        result.tests_passed and result.retries == 1 and result.test_runs == 2 and result.turns == 9
    )
    story = []
    for e in events:
        t, a = e["event_type"], e["attributes"]
        if t in (
            "file.read",
            "file.modified",
            "retry.attempted",
            "git.commit",
            "git.push",
            "llm.request.completed",
        ):
            story.append(t)
        elif t.startswith("shell.command.") and "test.framework" in a:
            story.append(f"tests:{a['test.failed']}")
    assert story[:4] == ["llm.request.completed", "file.read", "file.read", "llm.request.completed"]
    tests = [s for s in story if s.startswith("tests:")]
    assert tests == ["tests:1", "tests:0"]
    first_fail = story.index("tests:1")
    assert story[first_fail + 1 :].index("retry.attempted") < story[first_fail + 1 :].index(
        "file.modified"
    )
    assert story[-2:] == ["git.commit", "git.push"] or story[-3:-1] == ["git.commit", "git.push"]


def test_first_test_run_fails_exactly_one_named_test_and_the_second_passes(tmp_path: Path) -> None:
    events, _r, _root = play(tmp_path)
    closes = [
        e
        for e in events
        if e["event_type"].startswith("shell.command.") and "test.framework" in e["attributes"]
    ]
    assert [c["event_type"] for c in closes] == ["shell.command.failed", "shell.command.completed"]
    assert closes[0]["status"] == "error" and closes[0]["attributes"]["shell.exit_code"] == 1
    failing = closes[0]["attributes"]["test.failing"]
    assert len(failing) == 1 and "refresh_keeps_the_refresh_token" in failing[0]
    assert closes[1]["attributes"]["test.passed"] == 6


def test_commands_are_classified_and_the_push_is_external(tmp_path: Path) -> None:
    events, _r, _root = play(tmp_path)
    started = {
        e["attributes"]["shell.command"]: e["attributes"]
        for e in events
        if e["event_type"] == "shell.command.started"
    }
    assert started["tail -n 3300 logs/auth-server.log"]["shell.risk_class"] == "R0"
    assert started["python -m unittest discover -s tests -t ."]["shell.risk_class"] == "R1"
    assert any(a["shell.risk_class"] == "R2" and "push" in cmd for cmd, a in started.items())


def test_the_planted_secrets_and_the_environment_are_nowhere_in_the_events(tmp_path: Path) -> None:
    events, _r, _root = play(tmp_path)
    dump = repr(events)
    for secret in planted_literals():
        assert secret not in dump
    assert "ABB_DEMO_ENV_SECRET" not in dump


def test_the_log_is_large_enough_to_need_chunked_loading(tmp_path: Path) -> None:
    root = prepare_workspace(tmp_path)
    log = (root / "logs" / "auth-server.log").read_bytes()
    assert len(log) > 200_000 and log.count(b"\n") > LOG_LINES
    done = [e for e in play(tmp_path / "again")[0] if e["event_type"] == "shell.command.completed"]
    assert any(e["attributes"].get("shell.stdout_bytes", 0) > 200_000 for e in done)


def test_the_result_is_a_real_git_history_with_a_pushed_branch(tmp_path: Path) -> None:
    import subprocess

    _events, _r, root = play(tmp_path)
    remote = tmp_path / "origin.git"
    log = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--format=%s", "fix/oauth-session-expiry"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert log.stdout.splitlines()[0].startswith("Refresh sessions")
    assert (
        "logs/"
        not in subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    )


def test_tools_refuse_ambiguous_edits_and_paths_outside_the_workspace(tmp_path: Path) -> None:
    root = prepare_workspace(tmp_path)
    bb = BlackBox(mode="offline", project="d")
    with bb.run("r") as run:
        tools = Toolbox(CodingRecorder(bb, run, root))
        with pytest.raises(ToolError):
            tools.call("edit_file", {"path": "app/session.py", "old": "does not occur", "new": "x"})
        with pytest.raises(ToolError):
            tools.call("nope", {})
        with pytest.raises(ValueError):
            tools.call("read_file", {"path": "../../etc/passwd"})
        assert "no matches" in tools.call("search", {"pattern": "zzzz_not_there"}).text
        with pytest.raises(ToolError):
            tools.call("search", {"pattern": "("})


def test_a_failing_tool_does_not_stop_the_loop(tmp_path: Path) -> None:
    root = prepare_workspace(tmp_path)
    bb = BlackBox(mode="offline", project="d")
    bad = ScriptedModel(
        turns=[Reply("try", (ToolCall("c1", "read_file", {"path": "missing.py"}),)), Reply("done")]
    )
    with bb.run("r") as run:
        result = run_agent(bb, run, CodingRecorder(bb, run, root), bad)
    assert result.turns == 2 and not result.tests_passed
    failed = [e for e in bb.buffered_events() if e["event_type"] == "tool.call.failed"]
    assert len(failed) == 1


def test_real_model_mode_needs_the_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import run_demo

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    with pytest.raises(SystemExit) as exc:
        run_demo.main(["--model", "anthropic", "--offline"])
    assert exc.value.code == 2  # argparse error: refused before any model or command runs


def test_git_tools_refuse_flag_like_names(tmp_path: Path) -> None:
    root = prepare_workspace(tmp_path)
    bb = BlackBox(mode="offline", project="d")
    with bb.run("r") as run:
        tools = Toolbox(CodingRecorder(bb, run, root))
        with pytest.raises(ValueError):
            tools.call("git_branch", {"name": "--force"})
        with pytest.raises(ValueError):
            tools.call("git_push", {"branch": "-D"})


def test_search_masks_secrets_and_does_not_follow_links_to_secret_files(tmp_path):
    """Regression (third review): `search` returned file lines to the model unmasked, also via symlinks."""
    import subprocess

    from blackbox import BlackBox, PayloadMode
    from blackbox.coding import CodingRecorder

    from coding_agent.tools import Toolbox

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / ".env").write_text("DB_PW=search-secret-value-5512\nPORT=1\n")
    (tmp_path / "app.py").write_text("URL = 'search-secret-value-5512'\nOK = 'plain'\n")
    (tmp_path / "link.py").symlink_to(tmp_path / ".env")
    outside = tmp_path.parent / "outside_secret.py"
    outside.write_text("TOKEN = 'outside-secret-value-3301'\n")
    (tmp_path / "out.py").symlink_to(outside)
    bb = BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        box = Toolbox(CodingRecorder(bb, run, tmp_path))
        shown = box.call("search", {"pattern": "URL|OK|PW|TOKEN"}).text
    assert "search-secret-value-5512" not in shown and "outside-secret-value-3301" not in shown
    assert "OK = 'plain'" in shown  # ordinary hits still work


def test_tool_error_text_shown_to_the_model_is_masked(tmp_path: Path) -> None:
    """Regression (fourth review): a path or message with a known secret reached the model in tool errors."""
    planted = "err-path-secret-token-6619"
    root = prepare_workspace(tmp_path)
    (root / ".env").write_text(f"SVC_KEY={planted}\n")
    tool_texts: list[str] = []

    class Recording:
        provider, name = "scripted", "recording"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list, schemas: list) -> Reply:  # type: ignore[type-arg]
            self.calls += 1
            tool_texts.extend(
                r["text"] for m in messages if m["role"] == "tool" for r in m["results"]
            )
            if (
                self.calls == 1
            ):  # the model's own call carries the path it chose: that is its input, not a tool result
                return Reply(
                    "try", (ToolCall("c1", "read_file", {"path": f"missing-{planted}.py"}),)
                )
            return Reply("done")

    bb = BlackBox(mode="offline", project="d")
    with bb.run("r") as run:
        run_agent(bb, run, CodingRecorder(bb, run, root), Recording())  # type: ignore[arg-type]
    assert len(tool_texts) == 1 and tool_texts[0].startswith(
        "error:"
    )  # the failure reached the next prompt
    assert planted not in tool_texts[0]
