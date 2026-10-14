"""How recorded events map to action intents, for the dry run (ADR-140).

A dry run asks "what would this draft policy have decided about what the agents already did?".
The events carry what happened, so each relevant event is turned into the intent the SDK's
`guard()` would have sent. This table is the contract between the dry run and the integrations
(step 3 of Phase 14 calls `guard()` with the same capability, resource and risk class):

- `shell.command.started`: capability `shell.execute`, resource the executable name (below).
- `file.read`: `filesystem.read`, resource `file.path`.
- `file.created`, `file.modified`, `file.deleted`: `filesystem.write`, resource `file.path`.
- `git.push`: `github.push`, resource `git.push_target` (`<remote> <branch>`).
- `tool.call.started`: `tool.<name>`, resource `tool.operation` (empty when absent).

Only `*.started` is mapped for commands and tool calls, so a call is counted once, not again when
it completes. The risk class is `shell.risk_class` for commands and absent for everything else (a
rule with `risk_class` never matches an intent without one). Attributes a rule may match:
`shell.execute` has `category` (`shell.category`) and `command` (`shell.command`, the exact
command, omitted past 4,096 characters, where the SDK refuses to ask at all);
`filesystem.write` has `operation` (`created`, `modified`, `deleted`); `github.push` has
`branch` (`git.branch`); a tool call has none. There is no `github.merge` mapping because no
event type records a merge: a merge appears as
`shell.execute` (`gh pr merge`, `git merge`) and a rule on it matches the executable name.

The executable name is the first word of the command after leading `NAME=value` assignments and
the wrappers `sudo`, `env`, `nohup`, `time`, `nice`, reduced to its last path segment and cut to
128 characters. It is derived, not quoted: the dry run's response never contains it.
A tool name is mapped to a capability by replacing every character other than letters, digits,
`_`, `.` and `-` with `_` (at most 120 characters).
"""

import re
from collections.abc import Mapping
from typing import Any

from abb_api.policies.model import RISK_CLASSES, AttributeValue, Intent

MAPPED_EVENT_TYPES = (
    "shell.command.started",
    "file.read",
    "file.created",
    "file.modified",
    "file.deleted",
    "git.push",
    "tool.call.started",
)

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset({"sudo", "env", "nohup", "time", "nice"})
_UNSAFE_TOOL_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
MAX_EXECUTABLE = 128
MAX_COMMAND = 4096
MAX_TOOL_CAPABILITY = 120


def executable_of(command: str) -> str:
    for word in command.split():
        if _ASSIGNMENT.match(word) or word in _WRAPPERS:
            continue
        return word.rsplit("/", 1)[-1][:MAX_EXECUTABLE]
    return ""


def _text(attributes: Mapping[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) else None


def intent_for(event_type: str, attributes: Mapping[str, Any]) -> Intent | None:
    """The intent an event stands for, or None when the event is not a guardable action."""
    extra: dict[str, AttributeValue] = {}
    if event_type == "shell.command.started":
        risk = _text(attributes, "shell.risk_class")
        category = _text(attributes, "shell.category")
        if category:
            extra["category"] = category
        command = _text(attributes, "shell.command") or ""
        if command and len(command) <= MAX_COMMAND:
            extra["command"] = command  # the SDK sends the exact command too (review P2-3)
        return Intent(
            "shell.execute",
            executable_of(command),
            risk if risk in RISK_CLASSES else None,
            extra,
        )
    if event_type == "file.read":
        return Intent("filesystem.read", _text(attributes, "file.path") or "")
    if event_type in ("file.created", "file.modified", "file.deleted"):
        return Intent(
            "filesystem.write",
            _text(attributes, "file.path") or "",
            None,
            {"operation": event_type.rsplit(".", 1)[-1]},
        )
    if event_type == "git.push":
        branch = _text(attributes, "git.branch")
        if branch:
            extra["branch"] = branch
        return Intent("github.push", _text(attributes, "git.push_target") or "", None, extra)
    if event_type == "tool.call.started":
        name = _UNSAFE_TOOL_CHARS.sub("_", _text(attributes, "tool.name") or "")
        name = name[:MAX_TOOL_CAPABILITY].strip(".")
        if not name:
            return None
        return Intent(f"tool.{name}", _text(attributes, "tool.operation") or "")
    return None
