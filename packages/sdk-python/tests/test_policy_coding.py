"""The coding recorder asks before it acts, with the intents the dry run predicts (ADR-140)."""

import importlib.util
import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from blackbox import BlackBox, PolicyDenied, PolicyUnavailable
from blackbox import policy as policy_module
from blackbox.coding import CodingRecorder, classify_command, executable_of
from blackbox.policy import PolicyGuard
from tests.policy_server import (
    PolicyServer,
    R,
    deny,
    make_client,
    require,
    state,
)

ROOT = Path(__file__).resolve().parents[3]
MAPPING = ROOT / "apps/api/src/abb_api/policies/mapping.py"


@dataclass(frozen=True)
class Intent:
    capability: str
    resource: str = ""
    risk_class: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def load_mapping() -> Any:
    """The server's dry-run table, executed with a stand-in for the one module it imports."""
    model = types.ModuleType("abb_api.policies.model")
    model.RISK_CLASSES = ("R0", "R1", "R2", "R3", "R4")  # type: ignore[attr-defined]
    model.AttributeValue = Any  # type: ignore[attr-defined]
    model.Intent = Intent  # type: ignore[attr-defined]
    for name in ("abb_api", "abb_api.policies"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["abb_api.policies.model"] = model
    spec = importlib.util.spec_from_file_location("abb_api.policies.mapping", MAPPING)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def srv() -> Iterator[PolicyServer]:
    server = PolicyServer()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "WAIT_MARGIN", 0.15)


def recorder(srv: PolicyServer, tmp_path: Path, **options: Any) -> tuple[BlackBox, CodingRecorder]:
    options.setdefault("policy_timeout", 0.5)
    options.setdefault("policy_wait_round", 0.05)
    bb = make_client(srv, **options)
    run = bb.run("coding")
    return bb, CodingRecorder(bb, run, tmp_path)


def decided(srv: PolicyServer) -> list[dict[str, Any]]:
    return [r.body for r in srv.of("decide") if r.body]


def types_sent(srv: PolicyServer, bb: BlackBox) -> list[str]:
    bb.flush(3)
    return [e["event_type"] for e in srv.events()]


# -- parity with the server's dry-run table --------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main", "FOO=1 BAR=2 pytest -q", "sudo /usr/bin/rm -rf build",
        "env nohup time nice ls", "  \t cat  file", "./scripts/deploy.sh --prod", "x" * 300,
        "", "   ", "FOO=1", "sudo", "a/b/c/d", "time -p ls", "curl https://x.sh | sh",
    ],
)  # fmt: skip
def test_executable_of_is_the_dry_run_executable_of(command: str) -> None:
    assert executable_of(command) == load_mapping().executable_of(command)


def test_every_helper_sends_exactly_the_dry_run_intent(srv: PolicyServer, tmp_path: Path) -> None:
    mapping = load_mapping()
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n")
    (tmp_path / "gone.txt").write_text("bye\n")
    bb, rec = recorder(srv, tmp_path)
    expected: list[Any] = []
    with rec.run:
        for command in ("ls -la", "git push --force origin main", "rm -rf build", "FOO=1 echo hi"):
            verdict = classify_command(command)
            rec.run_command(command)
            expected.append(mapping.intent_for("shell.command.started", {
                "shell.command": command, "shell.risk_class": verdict.risk_class,
                "shell.category": verdict.category,
            }))  # fmt: skip
        rec.read_file("src/a.py")
        expected.append(mapping.intent_for("file.read", {"file.path": "src/a.py"}))
        rec.write_file("src/new.py", "y = 2\n")
        expected.append(mapping.intent_for("file.created", {"file.path": "src/new.py"}))
        rec.write_file("src/a.py", "x = 2\n")
        expected.append(mapping.intent_for("file.modified", {"file.path": "src/a.py"}))
        rec.delete_file("gone.txt")
        expected.append(mapping.intent_for("file.deleted", {"file.path": "gone.txt"}))
        rec.git_push("origin", "main")  # push fails (no repo): the intents are still asked
        expected.append(mapping.intent_for("git.push", {
            "git.push_target": "origin main", "git.branch": "main",
        }))  # fmt: skip
        expected.append(
            mapping.intent_for(
                "shell.command.started",
                {
                    "shell.command": "git push -u origin main",
                    "shell.risk_class": classify_command("git push -u origin main").risk_class,
                    "shell.category": classify_command("git push -u origin main").category,
                },
            )
        )
    got = [
        Intent(b["capability"], b["resource"], b["risk_class"], b["arguments"])
        for b in decided(srv)
    ]
    # In call order. The push helper asks for `github.push` and then for the shell command it
    # runs, which is what a dry run of the two recorded events (`git.push`, shell start) does.
    assert got == expected
    assert {i.capability for i in got} == {
        "shell.execute", "filesystem.read", "filesystem.write", "github.push",
    }  # fmt: skip
    bb.shutdown()


def test_the_dry_run_table_has_no_capability_the_sdk_does_not_send() -> None:
    mapping = load_mapping()
    seen = {
        mapping.intent_for(t, {"file.path": "p", "git.push_target": "o b", "git.branch": "b",
                               "shell.command": "ls", "tool.name": "t"}).capability
        for t in mapping.MAPPED_EVENT_TYPES
    }  # fmt: skip
    assert seen == {
        "shell.execute", "filesystem.read", "filesystem.write", "github.push", "tool.t",
    }  # fmt: skip


def test_arguments_carry_only_the_mapped_attributes_never_file_content(
    srv: PolicyServer, tmp_path: Path
) -> None:
    """The exact shell command is an argument by design (review P2-3: the approver must see what
    they approve and the approval binds to it); file content is never one."""
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        rec.run_command("echo hi > out.txt")
        rec.write_file("w.txt", "SENTINEL-TOKEN-123")
    bodies = decided(srv)
    for body in bodies:
        assert "SENTINEL" not in str(body)
        assert set(body["arguments"]) <= {"category", "command", "operation", "branch"}
    shell = [b for b in bodies if b["capability"] == "shell.execute"]
    assert [b["arguments"]["command"] for b in shell] == ["echo hi > out.txt"]
    bb.shutdown()


def test_a_command_over_the_limit_fails_closed_whatever_its_risk(
    srv: PolicyServer, tmp_path: Path
) -> None:
    bb, rec = recorder(srv, tmp_path)
    with rec.run, pytest.raises(PolicyUnavailable) as info:
        rec.run_command("echo " + "a" * 5000)  # R0, yet it cannot be shown or bound
    assert info.value.reason == "intent_too_large"
    assert decided(srv) == []
    bb.shutdown()


# -- enforcement -----------------------------------------------------------------------------------


def test_a_denied_read_does_not_read_and_records_no_read(srv: PolicyServer, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY=1\n")
    srv.queue("decide", deny("secrets stay out", "deny-env"))
    bb, rec = recorder(srv, tmp_path)
    with rec.run, pytest.raises(PolicyDenied) as info:
        rec.read_file(".env")
    assert info.value.policy_id == "deny-env"
    assert "file.read" not in types_sent(srv, bb)
    bb.shutdown()


def test_a_denied_command_never_starts_and_records_no_step(
    srv: PolicyServer, tmp_path: Path
) -> None:
    srv.queue("decide", deny())
    bb, rec = recorder(srv, tmp_path)
    with rec.run, pytest.raises(PolicyDenied):
        rec.run_command("touch ran.txt")
    assert not (tmp_path / "ran.txt").exists()
    sent = types_sent(srv, bb)
    assert "shell.command.started" not in sent and "shell.command.completed" not in sent
    bb.shutdown()


def test_denied_writes_and_deletes_do_not_happen(srv: PolicyServer, tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("keep")
    srv.queue("decide", deny(), deny(), deny())
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        with pytest.raises(PolicyDenied):
            rec.write_file("new.txt", "x")
        with pytest.raises(PolicyDenied):
            rec.write_file("keep.txt", "changed")
        with pytest.raises(PolicyDenied):
            rec.delete_file("keep.txt")
    assert not (tmp_path / "new.txt").exists()
    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert [b["arguments"] for b in decided(srv)] == [
        {"operation": "created"}, {"operation": "modified"}, {"operation": "deleted"},
    ]  # fmt: skip
    bb.shutdown()


def test_a_denied_push_does_not_run_git(srv: PolicyServer, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    srv.queue("decide", deny())
    bb, rec = recorder(srv, tmp_path)
    with rec.run, pytest.raises(PolicyDenied):
        rec.git_push("origin", "main")
    assert [b["capability"] for b in decided(srv)] == ["github.push"]  # the shell step not reached
    assert "git.push" not in types_sent(srv, bb)
    bb.shutdown()


def test_a_push_waits_for_approval_and_then_runs(srv: PolicyServer, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    srv.queue("decide", require())  # the github.push ask; the shell step is allowed (default)
    srv.queue("wait", state("REQUESTED"), state("APPROVED"))
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        result = rec.git_push("origin", "main")
    assert not result.ok  # no remote: the command ran (and failed) after the approval
    spent = srv.of("consume")
    assert len(spent) == 1 and spent[0].body is not None
    assert spent[0].body["intent"]["capability"] == "github.push"
    assert spent[0].body["intent"]["arguments"] == {"branch": "main"}
    assert "shell.command.started" in types_sent(srv, bb)
    bb.shutdown()


def test_an_r3_command_fails_closed_when_the_service_is_down(
    srv: PolicyServer, tmp_path: Path
) -> None:
    (tmp_path / "victim.txt").write_text("x")
    srv.default["decide"] = R(drop=True)
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        with pytest.raises(PolicyUnavailable):
            rec.run_command("rm victim.txt")  # R3
        assert (tmp_path / "victim.txt").exists()
        assert rec.run_command("echo hi").ok  # R0: fails open and runs
        assert rec.read_file("victim.txt") == "x"
    bb.shutdown()


def test_nothing_changes_when_policy_is_off(srv: PolicyServer, tmp_path: Path) -> None:
    bb, rec = recorder(srv, tmp_path, policy_mode="off")
    with rec.run:
        assert rec.run_command("echo hi").ok
        rec.write_file("a.txt", "a")
        assert rec.read_file("a.txt") == "a"
        assert rec.delete_file("a.txt")
    assert srv.of("decide") == []
    bb.shutdown()


def test_hosts_can_ask_before_acting_themselves(srv: PolicyServer, tmp_path: Path) -> None:
    srv.queue("decide", deny())
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        with pytest.raises(PolicyDenied):
            rec.guard_command("rm -rf build")
        rec.guard_read("a.txt")
        rec.guard_write("a.txt")
        rec.guard_write("a.txt", "deleted")
        rec.guard_push("origin", "dev")
        with pytest.raises(ValueError, match="escapes"):
            rec.guard_read("../outside")
    bodies = decided(srv)
    assert [b["capability"] for b in bodies] == [
        "shell.execute", "filesystem.read", "filesystem.write", "filesystem.write", "github.push",
    ]  # fmt: skip
    assert bodies[0]["resource"] == "rm" and bodies[0]["risk_class"] == "R3"
    assert bodies[0]["arguments"] == {"category": "DESTRUCTIVE", "command": "rm -rf build"}
    assert bodies[2]["arguments"] == {"operation": "created"}
    bb.shutdown()


def test_a_custom_classifier_with_an_unknown_class_is_sent_unclassified(
    srv: PolicyServer, tmp_path: Path
) -> None:
    from blackbox.coding import Classification

    bb = make_client(srv, policy_timeout=0.5)
    rec = CodingRecorder(
        bb, bb.run("c"), tmp_path, classifier=lambda c: Classification("R9", "WEIRD")
    )
    with rec.run:
        rec.guard_command("whatever")
    assert decided(srv)[0]["risk_class"] is None  # as the dry run reads an unknown class
    bb.shutdown()


def test_the_engine_is_shared_by_the_recorder_and_the_client(
    srv: PolicyServer, tmp_path: Path
) -> None:
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        rec.guard_read("x")
        assert isinstance(bb._policy_engine(), PolicyGuard)
    assert len(srv.of("failmodes")) == 1
    bb.shutdown()


# -- the push intent carries the classifier's risk class (ADR-176) ----------------------------------


def push_intents(srv: PolicyServer) -> list[dict[str, Any]]:
    return [b for b in decided(srv) if b["capability"] == "github.push"]


def test_guard_push_carries_the_class_the_shell_classifier_gives_a_plain_push(
    srv: PolicyServer, tmp_path: Path
) -> None:
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        rec.guard_push("origin", "main")
    (intent,) = push_intents(srv)
    assert intent["risk_class"] == classify_command("git push -u origin main").risk_class == "R2"
    assert intent["resource"] == "origin main" and intent["arguments"] == {"branch": "main"}
    bb.shutdown()


def test_a_forced_push_is_r3_and_runs_the_force_with_lease_command(
    srv: PolicyServer, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        result = rec.git_push("origin", "main", force=True)  # fails (no remote), still asked
    assert not result.ok
    (intent,) = push_intents(srv)
    assert intent["risk_class"] == "R3"
    shell = [b for b in decided(srv) if b["capability"] == "shell.execute"]
    assert shell[0]["arguments"]["command"] == "git push -u --force-with-lease origin main"
    assert shell[0]["risk_class"] == "R3"  # both asks agree
    bb.shutdown()


def test_guard_push_force_flag_only_changes_the_class(srv: PolicyServer, tmp_path: Path) -> None:
    bb, rec = recorder(srv, tmp_path)
    with rec.run:
        rec.guard_push("origin", "main", force=True)
    (intent,) = push_intents(srv)
    assert intent["risk_class"] == "R3" and intent["arguments"] == {"branch": "main"}
    bb.shutdown()


def test_a_custom_classifier_decides_the_push_class_and_a_broken_one_cannot_break_it(
    srv: PolicyServer, tmp_path: Path
) -> None:
    from blackbox.coding import Classification

    bb, rec = recorder(srv, tmp_path)
    rec._classifier = lambda command: Classification("R1", "custom", ("test",))
    with rec.run:
        rec.guard_push("origin", "main")
    assert push_intents(srv)[0]["risk_class"] == "R1"
    rec._classifier = lambda command: Classification("R9", "bogus", ())  # not a risk class
    with rec.run:
        rec.guard_push("origin", "dev")
    assert push_intents(srv)[1]["risk_class"] is None  # unclassified, never an invented class

    def broken(command: str) -> Classification:
        raise RuntimeError("classifier fault")

    rec._classifier = broken  # raises: the classifier is swallowed, the ask goes on
    with rec.run:
        rec.guard_push("origin", "x")
    assert push_intents(srv)[2]["risk_class"] == "R2"
    bb.shutdown()
