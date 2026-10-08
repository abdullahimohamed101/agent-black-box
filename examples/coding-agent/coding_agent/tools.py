"""The agent's tools. Each one runs through the CodingRecorder, so every effect is recorded."""

import re
from dataclasses import dataclass
from typing import Any

from blackbox.coding import CodingRecorder

MAX_RESULT_CHARS = 4000  # what the model sees of a tool result

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "read_file", "description": "Read a file in the repository.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search", "description": "Search the repository for a regular expression.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "edit_file", "description": "Replace one exact occurrence of `old` with `new` in a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"]}},
    {"name": "run_tests", "description": "Run the test suite.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_command", "description": "Run a shell command in the repository.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "git_diff", "description": "Show the working-tree diff.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "git_branch", "description": "Create and switch to a branch.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "git_commit", "description": "Commit all changes.",
     "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
    {"name": "git_push", "description": "Push the current branch to origin.",
     "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}}, "required": ["branch"]}},
]  # fmt: skip

TEST_COMMAND = "python -m unittest discover -s tests -t ."


@dataclass
class ToolOutcome:
    text: str
    ok: bool = True
    tests_failed: int | None = None  # set by run_tests
    failing: tuple[str, ...] = ()


class ToolError(Exception):
    """A tool failed in a way the model should see (bad path, ambiguous edit)."""


def _clip(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n... ({len(text) - MAX_RESULT_CHARS} more characters)"


class Toolbox:
    def __init__(self, rec: CodingRecorder) -> None:
        self.rec = rec

    def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            raise ToolError(f"unknown tool {name!r}")
        try:
            return handler(**args)  # type: ignore[no-any-return]
        except TypeError as exc:
            raise ToolError(f"bad arguments for {name}: {exc}") from exc

    def _read_file(self, path: str) -> ToolOutcome:
        return ToolOutcome(_clip(self.rec.read_file(path)))

    def _search(self, pattern: str) -> ToolOutcome:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid pattern: {exc}") from exc
        hits: list[str] = []
        for path in sorted(self.rec.root.rglob("*.py")):
            if ".git" in path.parts or "logs" in path.parts:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{self.rec.rel(path)}:{number}: {line.strip()}")
        return ToolOutcome(_clip("\n".join(hits[:50]) or "no matches"))

    def _edit_file(self, path: str, old: str, new: str) -> ToolOutcome:
        # Recorded as a read; the real, unredacted and complete content is needed to edit safely.
        current = self.rec.read_file(path, None, redact=False)
        if current.count(old) != 1:
            raise ToolError(f"`old` must occur exactly once in {path} (found {current.count(old)})")
        self.rec.write_file(path, current.replace(old, new))
        return ToolOutcome(f"edited {path}")

    def _run_tests(self) -> ToolOutcome:
        result = self.rec.run_command(TEST_COMMAND)
        failed = result.test.failed if result.test else (0 if result.ok else 1)
        failing = result.test.failing if result.test else ()
        return ToolOutcome(_clip(result.output), result.ok, failed, failing)

    def _run_command(self, command: str) -> ToolOutcome:
        result = self.rec.run_command(command)
        return ToolOutcome(_clip(result.output), result.ok)

    def _git_diff(self) -> ToolOutcome:
        return ToolOutcome(_clip(self.rec.git_diff()) or "no changes")

    def _git_branch(self, name: str) -> ToolOutcome:
        result = self.rec.git_branch(name)
        return ToolOutcome(_clip(result.output), result.ok)

    def _git_commit(self, message: str) -> ToolOutcome:
        result = self.rec.git_commit(message)
        return ToolOutcome(_clip(result.output), result.ok)

    def _git_push(self, branch: str) -> ToolOutcome:
        result = self.rec.git_push("origin", branch)
        return ToolOutcome(_clip(result.output), result.ok)
