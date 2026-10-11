"""The offline event fixture of the two demo runs the comparison tests align (Phase 11, AC-6).

`baseline` fixes the bug (the second edit is `app/session.py`, the tests pass, the run commits and
pushes); `wrong-fix` makes the same first moves, then edits `app/oauth.py` instead, the tests still
fail and the run ends in error. The events have the shapes the coding recorder emits (a tool span
around file reads and writes and shell commands, `retry.attempted` before a second edit, git
events), fixed ids, and different timestamps and scratch directories per run.

    uv run python ../scripts/gen_compare_fixture.py --out tests/fixtures/compare_pair
    uv run python ../scripts/gen_compare_fixture.py --check tests/fixtures/compare_pair

`manifest.json` names the events the tests assert on, by id; it is written here, never by hand.
`same_file_edit=True` (the mutation of AC-6) makes the wrong-fix edit the same file as the baseline.
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from abb_event_schema.ids import IdGenerator, IdKind

FIXED_MS = int(datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)
MODEL = {"llm.provider": "scripted", "llm.model": "scripted-agent-1"}
TEST_COMMAND = "python -m unittest discover -s tests -t ."
Event = dict[str, Any]


class Recorder:
    """Emits the wire events of one run, advancing a clock only through `dt`."""

    def __init__(self, name: str, start: datetime, workdir: str) -> None:
        digest = hashlib.sha256(name.encode()).digest()
        self._ids = IdGenerator(clock_ms=lambda: FIXED_MS, entropy=lambda n: digest[:n])
        self.run_id = self._ids.new_id(IdKind.RUN)
        self.trace_id = self._ids.new_id(IdKind.TRACE)
        self.now, self.seq, self.workdir = start, 0, workdir
        self.events: list[Event] = []
        self.root = self._ids.new_id(IdKind.SPAN)
        self.names: dict[str, str] = {}

    def emit(self, event_type: str, *, span: str | None = None, status: str | None = None,
             attrs: dict[str, Any] | None = None, dt: float = 0.4,
             duration_ms: float | None = None, name: str | None = None) -> Event:  # fmt: skip
        self.now += timedelta(seconds=dt)
        self.seq += 1
        event: Event = {
            "schema_version": "1.0",
            "event_id": self._ids.new_id(IdKind.EVENT),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": span,
            "parent_span_id": self.root if span and span != self.root else None,
            "agent_id": "coding-agent",
            "event_type": event_type,
            "occurred_at": self.now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sequence": self.seq,
            "status": status,
            "duration_ms": duration_ms,
            "attributes": attrs or {},
        }
        event = {k: v for k, v in event.items() if v is not None}
        self.events.append(event)
        if name:
            self.names[name] = event["event_id"]
        return event

    def span(self) -> str:
        return self._ids.new_id(IdKind.SPAN)

    def llm(self) -> None:
        span = self.span()
        self.emit("llm.request.started", span=span, attrs=dict(MODEL))
        self.emit("llm.request.completed", span=span, status="success", duration_ms=900,
                  attrs={**MODEL, "llm.input_tokens": 1200, "llm.output_tokens": 90}, dt=0.9)  # fmt: skip

    def tool(self, name: str, inner: Any = None) -> None:
        span = self.span()
        self.emit("tool.call.started", span=span, attrs={"tool.name": name})
        if inner is not None:
            inner(span)
        self.emit("tool.call.completed", span=span, status="success", duration_ms=300,
                  attrs={"tool.name": name}, dt=0.3)  # fmt: skip

    def read(self, path: str, *, name: str | None = None) -> Any:
        def inner(span: str) -> None:
            self.emit("file.read", span=span, name=name, attrs={
                "file.path": path, "file.operation": "read", "file.language": "python"})  # fmt: skip

        return inner

    def edit(self, path: str, *, read_name: str | None, modified_name: str | None) -> Any:
        def inner(span: str) -> None:
            self.emit("file.read", span=span, name=read_name, attrs={
                "file.path": path, "file.operation": "read", "file.language": "python"})  # fmt: skip
            self.emit("file.modified", span=span, name=modified_name, attrs={
                "file.path": path, "file.operation": "modified", "file.language": "python",
                "file.lines_added": 1, "file.lines_removed": 1})  # fmt: skip

        return inner

    def shell(self, command: str, *, ok: bool = True, tests: bool = False,
              name: str | None = None) -> Any:  # fmt: skip
        def inner(span: str) -> None:
            sh = self.span()
            attrs: dict[str, Any] = {"shell.command": command, "shell.cwd": self.workdir}
            self.emit("shell.command.started", span=sh, attrs=attrs)
            done = {**attrs, "shell.exit_code": 0 if ok else 1}
            if tests:
                done.update({"test.framework": "unittest", "test.total": 6,
                             "test.failed": 0 if ok else 1, "test.passed": 6 if ok else 5})  # fmt: skip
            self.emit("shell.command.completed", span=sh, status="success" if ok else "error",
                      attrs=done, duration_ms=1800, dt=1.8, name=name)  # fmt: skip

        return inner

    def git(self, event_type: str, attrs: dict[str, Any]) -> Any:
        def inner(span: str) -> None:
            self.emit(event_type, span=span, attrs=attrs)

        return inner

    def retry(self, attempt: int) -> None:
        self.emit(
            "retry.attempted", attrs={"retry.attempt": attempt, "retry.reason": "tests failed"}
        )


def build(variant: str, *, same_file_edit: bool = False) -> tuple[list[Event], dict[str, str]]:
    """The wire events and the named event ids of one run: variant `baseline` or `wrong-fix`."""
    fixed = variant == "baseline"
    start = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
    if not fixed:
        start += timedelta(hours=2, minutes=17)  # a different clock and a different scratch dir
    workdir = "/tmp/abb-" + ("a1b2c3d4" if fixed else "9f8e7d6c") + "/repo"
    r = Recorder(variant, start, workdir)
    r.emit("run.started", attrs={"run.name": "fix-session-expiry"}, dt=0)
    r.emit("agent.started", span=r.root)
    r.llm()
    r.tool("read_file", r.read("app/session.py"))
    r.tool("read_file", r.read("tests/test_session.py"))
    r.llm()
    r.tool("search")
    r.llm()
    r.tool("edit_file", r.edit("app/session.py", read_name=None, modified_name=None))
    r.llm()
    r.tool("run_tests", r.shell(TEST_COMMAND, ok=False, tests=True, name="first_test_run"))
    r.llm()
    r.tool("run_command", r.shell("tail -n 3300 logs/auth-server.log"))
    r.tool("read_file", r.read("app/oauth.py"))
    r.llm()
    r.retry(1)
    second = "app/session.py" if fixed or same_file_edit else "app/oauth.py"
    r.tool("edit_file", r.edit(second, read_name="second_edit_read",
                               modified_name="second_edit_modified"))  # fmt: skip
    r.llm()
    r.tool("run_tests", r.shell(TEST_COMMAND, ok=fixed, tests=True, name="second_test_run"))
    r.llm()
    if fixed:
        r.tool("git_diff", r.git("git.diff", {"git.files_changed": 1}))
        r.tool(
            "git_branch", r.git("git.branch_created", {"git.branch": "fix/oauth-session-expiry"})
        )
        r.tool("git_commit", r.git("git.commit", {"git.branch": "fix/oauth-session-expiry"}))
        r.tool("git_push", r.git("git.push", {"git.branch": "fix/oauth-session-expiry"}))
        r.llm()
    r.emit("agent.completed", span=r.root, status="success" if fixed else "error")
    r.emit("run.completed", status="success" if fixed else "error")
    return r.events, r.names


def render() -> dict[str, str]:
    """File name -> text of the whole fixture."""
    files: dict[str, str] = {}
    manifest: dict[str, Any] = {}
    for variant, key in (("baseline", "baseline"), ("wrong-fix", "wrong_fix")):
        events, names = build(variant)
        files[f"{key}.ndjson"] = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
        manifest[key] = {"run_id": events[0]["run_id"], "events": len(events), **names}
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="write the fixture files here")
    parser.add_argument("--check", type=Path, help="exit 1 if the files here differ")
    args = parser.parse_args()
    files = render()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (args.out / name).write_text(text)
    if args.check:
        stale = [n for n, t in files.items() if (args.check / n).read_text() != t]
        if stale:
            print("stale fixture files:", ", ".join(stale), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
