"""Parse and validate a policy document (spec §151, ADR-140).

The document is YAML, read with PyYAML's event stream only: no object is ever constructed from
the input (`yaml.load` and friends are never called), so a tag cannot name a class. On top of
that the reader rejects, before they can cost anything: documents over `MAX_DOCUMENT_BYTES`
(checked first), control and format characters, anchors, aliases (so no expansion attack), tags,
merge keys, a second document, nesting deeper than `MAX_YAML_DEPTH` and more than
`MAX_YAML_NODES` nodes. Scalars are typed by this module's own rules (`true`/`false`, integers,
decimals, `null`), not YAML 1.1's: `no`, `on`, `1:30` and `0777` stay text.

Every problem is an `Issue(line, column, code, message)`. Messages are fixed sentences. They may
name the offending *key* (cut to 64 characters) but never a value, so an error cannot echo the
document back (a policy can be long and was typed by a person who may have pasted a secret).
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)

from abb_api.policies.glob import compile_glob
from abb_api.policies.model import (
    APPROVER_ROLES,
    DEFAULT_EXPIRES_SECONDS,
    MAX_ATTRIBUTE_VALUE_LENGTH,
    MAX_ATTRIBUTES_PER_RULE,
    MAX_DESCRIPTION_LENGTH,
    MAX_DOCUMENT_BYTES,
    MAX_EXPIRES_SECONDS,
    MAX_GLOB_LENGTH,
    MAX_RULES,
    MAX_YAML_DEPTH,
    MAX_YAML_NODES,
    MIN_EXPIRES_SECONDS,
    RISK_CLASSES,
    AttributeValue,
    DecisionKind,
    ExpiryAction,
    FailMode,
    Policy,
    Rule,
    When,
)

MAX_ISSUES = 50
MAX_KEY_NAME = 64

_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,5}(?:\.\*)?")
_MAX_CAPABILITY_LENGTH = 128
_RULE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_ATTRIBUTE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_DURATION = re.compile(r"([0-9]{1,5})([mhd])")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_INT = re.compile(r"[-+]?[0-9]{1,18}")
_FLOAT = re.compile(r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})
_NULL = frozenset({"", "~", "null", "Null", "NULL"})

TOP_KEYS = frozenset({"version", "policies", "default"})
RULE_KEYS = frozenset(
    {
        "id",
        "description",
        "when",
        "decision",
        "approver_role",
        "expires_in",
        "on_expiry",
        "on_error",
        "allow_self_approval",
    }
)
APPROVAL_ONLY_KEYS = ("approver_role", "expires_in", "on_expiry", "allow_self_approval")
WHEN_KEYS = frozenset({"capability", "resource", "risk_class", "attributes"})


# Every `Issue.code` this module can produce (the API documents them; a test covers each one).
ISSUE_CODES = frozenset(
    {
        "document_too_large", "invalid_character", "empty_document", "syntax_error",
        "multiple_documents", "anchor_not_allowed", "alias_not_allowed", "tag_not_allowed",
        "merge_key_not_allowed", "too_deep", "too_many_nodes", "wrong_type", "unknown_key",
        "duplicate_key", "missing_key", "invalid_version", "invalid_default", "too_many_rules",
        "invalid_rule_id", "duplicate_rule_id", "invalid_description", "empty_when",
        "invalid_capability", "glob_too_long", "invalid_risk_class", "too_many_attributes",
        "invalid_attribute", "invalid_duration", "duration_out_of_range", "invalid_value",
        "field_not_allowed", "invalid_role", "too_many_errors",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Issue:
    line: int
    column: int
    code: str
    message: str


@dataclass(frozen=True)
class ParseResult:
    policy: Policy | None
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return self.policy is not None and not self.issues


@dataclass
class _Node:
    kind: str  # "scalar" | "map" | "seq"
    line: int
    column: int
    value: str = ""  # scalars
    plain: bool = False  # scalars written without quotes
    items: list["_Node"] | None = None  # seq
    pairs: list[tuple["_Node", "_Node"]] | None = None  # map


class _Stop(Exception):
    """Reading cannot go on; the issue is already recorded."""


class _Issues:
    def __init__(self) -> None:
        self.found: list[Issue] = []

    def add(self, node: _Node | tuple[int, int], code: str, message: str) -> None:
        if len(self.found) >= MAX_ISSUES:
            return
        line, column = (node.line, node.column) if isinstance(node, _Node) else node
        self.found.append(Issue(line, column, code, message))
        if len(self.found) == MAX_ISSUES:
            self.found.append(Issue(line, column, "too_many_errors", "Too many errors; stopping."))


def _name(node: _Node) -> str:
    text = node.value if node.kind == "scalar" else "?"
    return text if len(text) <= MAX_KEY_NAME else text[: MAX_KEY_NAME - 1] + "…"


# --------------------------------------------------------------------------- reading the text


def _prescan(text: str, issues: _Issues) -> None:
    line, column = 1, 1
    for ch in text:
        if ch == "\n":
            line, column = line + 1, 1
            continue
        category = unicodedata.category(ch)
        if (ch not in "\r\t" and category == "Cc") or category in ("Cf", "Cs", "Zl", "Zp"):
            issues.add(
                (line, column),
                "invalid_character",
                "Control and invisible formatting characters are not allowed.",
            )
            return
        column += 1


def _position(text: str, index: int) -> tuple[int, int]:
    before = text[: max(0, index)]
    return before.count("\n") + 1, len(before) - (before.rfind("\n") + 1) + 1


def _read(text: str, issues: _Issues) -> _Node | None:
    """The document as a tree, or None with the reason in `issues`."""
    root: _Node | None = None
    stack: list[tuple[_Node, _Node | None]] = []  # (container, key waiting for its value)
    documents = nodes = 0

    def place(child: _Node) -> None:
        nonlocal root
        if not stack:
            root = child
            return
        parent, pending = stack[-1]
        if parent.kind == "seq":
            assert parent.items is not None
            parent.items.append(child)
        elif pending is None:
            stack[-1] = (parent, child)
        else:
            assert parent.pairs is not None
            parent.pairs.append((pending, child))
            stack[-1] = (parent, None)

    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            mark = getattr(event, "start_mark", None)
            at = (mark.line + 1, mark.column + 1) if mark is not None else (1, 1)
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    issues.add(at, "multiple_documents", "Only one YAML document is allowed.")
                    raise _Stop
                continue
            if isinstance(event, AliasEvent):
                issues.add(at, "alias_not_allowed", "YAML aliases are not allowed.")
                raise _Stop
            if getattr(event, "anchor", None) is not None:
                issues.add(at, "anchor_not_allowed", "YAML anchors are not allowed.")
                raise _Stop
            if getattr(event, "tag", None) is not None:
                issues.add(at, "tag_not_allowed", "YAML tags are not allowed.")
                raise _Stop
            if isinstance(event, ScalarEvent | MappingStartEvent | SequenceStartEvent):
                nodes += 1
                if nodes > MAX_YAML_NODES:
                    issues.add(at, "too_many_nodes", "The document has too many entries.")
                    raise _Stop
            if isinstance(event, ScalarEvent):
                plain = event.style is None
                place(_Node("scalar", at[0], at[1], value=event.value, plain=plain))
            elif isinstance(event, MappingStartEvent | SequenceStartEvent):
                if len(stack) >= MAX_YAML_DEPTH:
                    issues.add(at, "too_deep", "The document is nested too deeply.")
                    raise _Stop
                is_map = isinstance(event, MappingStartEvent)
                node = _Node(
                    "map" if is_map else "seq",
                    at[0],
                    at[1],
                    items=None if is_map else [],
                    pairs=[] if is_map else None,
                )
                place(node)
                stack.append((node, None))
            elif isinstance(event, MappingEndEvent | SequenceEndEvent):
                stack.pop()
    except _Stop:
        return None
    except yaml.reader.ReaderError as error:
        issues.add(_position(text, error.position), "invalid_character", "Invalid character.")
        return None
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark
        at = (mark.line + 1, mark.column + 1) if mark is not None else (1, 1)
        issues.add(at, "syntax_error", "This is not valid YAML here.")
        return None
    except yaml.YAMLError:
        issues.add((1, 1), "syntax_error", "This is not valid YAML.")
        return None
    if root is None:
        issues.add((1, 1), "empty_document", "The policy is empty.")
    return root


# --------------------------------------------------------------------------- typed scalars


def _scalar(node: _Node) -> Any:
    """str | bool | int | float | None by this module's rules (not YAML 1.1's)."""
    if not node.plain:
        return node.value
    v = node.value
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    if v in _NULL:
        return None
    if _INT.fullmatch(v):
        return int(v)
    if _FLOAT.fullmatch(v):
        number = float(v)
        return number if math.isfinite(number) else v
    return v


def _text(node: _Node, issues: _Issues, what: str) -> str | None:
    value = _scalar(node) if node.kind == "scalar" else None
    if node.kind != "scalar" or not isinstance(value, str) or value == "":
        issues.add(node, "wrong_type", f"{what} must be text.")
        return None
    return value


def _mapping(node: _Node, issues: _Issues, what: str) -> dict[str, tuple[_Node, _Node]] | None:
    if node.kind != "map":
        issues.add(node, "wrong_type", f"{what} must be a mapping of keys to values.")
        return None
    assert node.pairs is not None
    found: dict[str, tuple[_Node, _Node]] = {}
    for key, value in node.pairs:
        if key.kind != "scalar" or (not key.plain and key.value == ""):
            issues.add(key, "wrong_type", "A key must be a plain word.")
            continue
        if key.plain and key.value == "<<":
            issues.add(key, "merge_key_not_allowed", "YAML merge keys are not allowed.")
            continue
        if key.value in found:
            issues.add(key, "duplicate_key", f"Key '{_name(key)}' is given twice.")
            continue
        found[key.value] = (key, value)
    return found


def _unknown(
    found: dict[str, tuple[_Node, _Node]], allowed: frozenset[str], issues: _Issues
) -> None:
    for name, (key, _) in found.items():
        if name not in allowed:
            issues.add(key, "unknown_key", f"Unknown key '{_name(key)}'.")


# --------------------------------------------------------------------------- rules


def _when(node: _Node, issues: _Issues) -> When | None:
    found = _mapping(node, issues, "'when'")
    if found is None:
        return None
    _unknown(found, WHEN_KEYS, issues)
    capability = resource = None
    risks: tuple[str, ...] = ()
    attributes: tuple[tuple[str, AttributeValue], ...] = ()
    ok = True
    if "capability" in found:
        value = found["capability"][1]
        text = _text(value, issues, "'capability'")
        if text is not None and (
            len(text) > _MAX_CAPABILITY_LENGTH or not _CAPABILITY.fullmatch(text)
        ):
            issues.add(
                value,
                "invalid_capability",
                "A capability is dotted words such as 'filesystem.read', or 'filesystem.*' for "
                "everything below one.",
            )
            text = None
        capability, ok = text, ok and text is not None
    if "resource" in found:
        value = found["resource"][1]
        text = _text(value, issues, "'resource'")
        if text is not None and len(text) > MAX_GLOB_LENGTH:
            issues.add(
                value,
                "glob_too_long",
                f"A resource pattern is at most {MAX_GLOB_LENGTH} characters.",
            )
            text = None
        resource, ok = text, ok and text is not None
    if "risk_class" in found:
        parsed = _risks(found["risk_class"][1], issues)
        risks, ok = parsed or (), ok and parsed is not None
    if "attributes" in found:
        parsed_attributes = _attributes(found["attributes"][1], issues)
        attributes, ok = parsed_attributes or (), ok and parsed_attributes is not None
    if not any(name in found and not _is_null(found[name][1]) for name in WHEN_KEYS):
        issues.add(node, "empty_when", "'when' needs at least one condition.")
        return None
    return (
        When(
            capability, compile_glob(resource) if resource is not None else None, risks, attributes
        )
        if ok
        else None
    )


def _is_null(node: _Node) -> bool:
    return node.kind == "scalar" and _scalar(node) is None


def _risks(node: _Node, issues: _Issues) -> tuple[str, ...] | None:
    nodes = node.items if node.kind == "seq" and node.items is not None else [node]
    if not nodes or len(nodes) > len(RISK_CLASSES) * 2:
        issues.add(node, "invalid_risk_class", "'risk_class' lists one to five of R0 to R4.")
        return None
    classes: list[str] = []
    for item in nodes:
        value = _scalar(item) if item.kind == "scalar" else None
        if not isinstance(value, str) or value not in RISK_CLASSES:
            issues.add(item, "invalid_risk_class", "A risk class is one of R0, R1, R2, R3, R4.")
            return None
        if value not in classes:
            classes.append(value)
    return tuple(classes)


def _attributes(node: _Node, issues: _Issues) -> tuple[tuple[str, AttributeValue], ...] | None:
    found = _mapping(node, issues, "'attributes'")
    if found is None:
        return None
    if len(found) > MAX_ATTRIBUTES_PER_RULE:
        issues.add(
            node, "too_many_attributes", f"At most {MAX_ATTRIBUTES_PER_RULE} attributes per rule."
        )
        return None
    parsed: list[tuple[str, AttributeValue]] = []
    for name, (key, value) in found.items():
        if not _ATTRIBUTE_KEY.fullmatch(name):
            issues.add(key, "invalid_attribute", "An attribute name is letters, digits, _ . -.")
            return None
        scalar = _scalar(value) if value.kind == "scalar" else None
        if scalar is None or (isinstance(scalar, str) and len(scalar) > MAX_ATTRIBUTE_VALUE_LENGTH):
            issues.add(
                value,
                "invalid_attribute",
                f"An attribute value is text, a number or true/false (text up to "
                f"{MAX_ATTRIBUTE_VALUE_LENGTH} characters).",
            )
            return None
        parsed.append((name, scalar))
    return tuple(sorted(parsed, key=lambda kv: kv[0]))


def _duration(node: _Node, issues: _Issues) -> int | None:
    text = _text(node, issues, "'expires_in'")
    if text is None:
        return None
    match = _DURATION.fullmatch(text)
    if match is None:
        issues.add(node, "invalid_duration", "A duration is a number and m, h or d, such as 15m.")
        return None
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if not MIN_EXPIRES_SECONDS <= seconds <= MAX_EXPIRES_SECONDS:
        issues.add(node, "duration_out_of_range", "'expires_in' is between 1m and 7d.")
        return None
    return seconds


def _choice(node: _Node, issues: _Issues, key: str, options: tuple[str, ...]) -> str | None:
    text = _text(node, issues, f"'{key}'")
    if text is None:
        return None
    if text not in options:
        issues.add(node, "invalid_value", f"'{key}' is one of: {', '.join(options)}.")
        return None
    return text


def _rule(node: _Node, issues: _Issues, seen: dict[str, int]) -> Rule | None:
    found = _mapping(node, issues, "A rule")
    if found is None:
        return None
    _unknown(found, RULE_KEYS, issues)
    before = len(issues.found)
    for required in ("id", "when", "decision"):
        if required not in found:
            issues.add(node, "missing_key", f"A rule needs '{required}'.")

    rule_id = None
    if "id" in found:
        id_node = found["id"][1]
        text = _text(id_node, issues, "'id'")
        if text is not None and not _RULE_ID.fullmatch(text):
            issues.add(
                id_node,
                "invalid_rule_id",
                "A rule id is lowercase letters, digits, '-' and '_' (at most 64).",
            )
        elif text is not None:
            if text in seen:
                issues.add(
                    id_node,
                    "duplicate_rule_id",
                    f"This rule id is already used by the rule on line {seen[text]}.",
                )
            else:
                seen[text] = id_node.line
                rule_id = text

    description = ""
    if "description" in found and not _is_null(found["description"][1]):
        d_node = found["description"][1]
        text = _text(d_node, issues, "'description'")
        if text is not None:
            text = text.strip()
            if len(text) > MAX_DESCRIPTION_LENGTH or any(ord(c) < 32 for c in text):
                issues.add(
                    d_node,
                    "invalid_description",
                    f"A description is one line of at most {MAX_DESCRIPTION_LENGTH} characters.",
                )
            else:
                description = text

    when = _when(found["when"][1], issues) if "when" in found else None

    decision = None
    if "decision" in found:
        picked = _choice(
            found["decision"][1], issues, "decision", tuple(k.value for k in DecisionKind)
        )
        decision = DecisionKind(picked) if picked else None
    if decision is not None and decision is not DecisionKind.REQUIRE_APPROVAL:
        for name in APPROVAL_ONLY_KEYS:
            if name in found:
                issues.add(
                    found[name][0],
                    "field_not_allowed",
                    f"'{name}' only applies to require_approval.",
                )

    approver = expires = on_expiry = None
    self_approval = False
    if decision is DecisionKind.REQUIRE_APPROVAL:
        if "approver_role" in found and not _is_null(found["approver_role"][1]):
            role = _text(found["approver_role"][1], issues, "'approver_role'")
            if role is not None:
                if role.upper() in APPROVER_ROLES:
                    approver = role.upper()
                else:
                    issues.add(
                        found["approver_role"][1],
                        "invalid_role",
                        "'approver_role' is one of: "
                        + ", ".join(r.lower() for r in APPROVER_ROLES)
                        + ".",
                    )
        expires = DEFAULT_EXPIRES_SECONDS
        if "expires_in" in found:
            expires = _duration(found["expires_in"][1], issues)
        expiry = ExpiryAction.DENY.value
        if "on_expiry" in found:
            expiry_pick = _choice(
                found["on_expiry"][1], issues, "on_expiry", tuple(e.value for e in ExpiryAction)
            )
            expiry = expiry_pick or expiry
        on_expiry = ExpiryAction(expiry)
        if "allow_self_approval" in found:
            flag = _scalar(found["allow_self_approval"][1])
            if isinstance(flag, bool):
                self_approval = flag
            else:
                issues.add(
                    found["allow_self_approval"][1],
                    "wrong_type",
                    "'allow_self_approval' is true or false.",
                )

    on_error = None
    if "on_error" in found and not _is_null(found["on_error"][1]):
        pick = _choice(found["on_error"][1], issues, "on_error", tuple(m.value for m in FailMode))
        on_error = FailMode(pick) if pick else None

    if len(issues.found) > before or rule_id is None or when is None or decision is None:
        return None
    return Rule(
        rule_id, description, when, decision, approver, expires, on_expiry, on_error, self_approval
    )


# --------------------------------------------------------------------------- the document


def _policy(root: _Node, issues: _Issues) -> Policy | None:
    top = _mapping(root, issues, "The policy")
    if top is None:
        return None
    _unknown(top, TOP_KEYS, issues)
    before = len(issues.found)
    if "version" not in top:
        issues.add(root, "missing_key", "The policy needs 'version: 1'.")
    else:
        v_node = top["version"][1]
        version = _scalar(v_node) if v_node.kind == "scalar" else None
        if isinstance(version, bool) or version != 1:
            issues.add(v_node, "invalid_version", "Only 'version: 1' is supported.")

    default = DecisionKind.ALLOW
    if "default" in top:
        d_node = top["default"][1]
        picked = _text(d_node, issues, "'default'")
        if picked is not None:
            if picked in ("allow", "deny"):
                default = DecisionKind(picked)
            else:
                issues.add(d_node, "invalid_default", "'default' is allow or deny.")

    rules: list[Rule] = []
    if "policies" in top and not _is_null(top["policies"][1]):
        list_node = top["policies"][1]
        if list_node.kind != "seq" or list_node.items is None:
            issues.add(list_node, "wrong_type", "'policies' must be a list of rules.")
        elif len(list_node.items) > MAX_RULES:
            issues.add(list_node, "too_many_rules", f"A policy has at most {MAX_RULES} rules.")
        else:
            seen: dict[str, int] = {}
            for item in list_node.items:
                rule = _rule(item, issues, seen)
                if rule is not None:
                    rules.append(rule)
    return None if len(issues.found) > before else Policy(tuple(rules), default)


def parse_policy(text: str) -> ParseResult:
    """Validate `text` and compile it. `policy` is set exactly when there are no issues."""
    issues = _Issues()
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        issues.add((1, 1), "invalid_character", "The text is not valid Unicode.")
        return ParseResult(None, tuple(issues.found))
    if size > MAX_DOCUMENT_BYTES:
        issues.add(
            (1, 1),
            "document_too_large",
            f"A policy is at most {MAX_DOCUMENT_BYTES // 1024} KiB.",
        )
        return ParseResult(None, tuple(issues.found))
    _prescan(text, issues)
    if issues.found:
        return ParseResult(None, tuple(issues.found))
    root = _read(text, issues)
    if root is None:
        return ParseResult(None, tuple(issues.found))
    policy = _policy(root, issues)
    if issues.found:
        return ParseResult(None, tuple(issues.found))
    return ParseResult(policy, ())


@lru_cache(maxsize=128)
def compile_cached(text: str) -> Policy | None:
    """The compiled policy of a stored document (immutable text, so the cache never goes stale)."""
    return parse_policy(text).policy


def rule_count(text: str) -> int:
    """Rules in a document that has already been validated."""
    policy = compile_cached(text)
    return len(policy.rules) if policy else 0
