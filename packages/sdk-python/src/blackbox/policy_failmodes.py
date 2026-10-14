"""What to do when the policy service cannot answer (D3, ADR-141 point 9).

Open means the action proceeds (and a `policy.warning` is recorded); closed means it is refused
(`PolicyUnavailable`). The order of authority, first hit wins:

1. the `on_error=` argument of the call,
2. the operator's per-capability override (`policy_fail_modes`, `BLACKBOX_POLICY_FAIL_MODES`),
3. the server's fail-mode table, cached before the outage (a rule's own `on_error`),
4. the risk class: R0-R2 and unclassified open, R3-R4 closed.

The cached table is an approximation by design (it cannot carry attribute conditions or the order
between a listed and an unlisted rule): a listed rule with attribute conditions is only believed
when it says `closed`, and an unlisted one is ignored.
"""

import re
from dataclasses import dataclass
from typing import Any

from blackbox.policy_glob import Glob, compile_glob

OPEN, CLOSED = "open", "closed"
MODES = (OPEN, CLOSED)
_RISK_DEFAULTS = {"R0": OPEN, "R1": OPEN, "R2": OPEN, "R3": CLOSED, "R4": CLOSED}
MAX_TABLE_RULES = 256
_MAX_PATTERN = 512


def default_mode(risk_class: str | None) -> str:
    """The built-in default: R3 and R4 closed, everything else (R0-R2, unclassified) open."""
    return _RISK_DEFAULTS.get(risk_class or "", OPEN)


def capability_matches(pattern: str, capability: str) -> bool:
    """Exact, or `prefix.*` for anything below `prefix.` (the policy language's own rule)."""
    if pattern.endswith(".*"):
        prefix = pattern[:-1]
        return capability.startswith(prefix) and len(capability) > len(prefix)
    return pattern == capability


def parse_modes(text: str | None) -> dict[str, str]:
    """`shell.execute=closed,filesystem.read=open` -> {capability: mode}; bad ones are skipped."""
    found: dict[str, str] = {}
    for item in re.split(r"[,\s]+", text or ""):
        name, _, mode = item.partition("=")
        if name and mode.lower() in MODES and len(name) <= 128:
            found[name] = mode.lower()
    return found


def override_for(overrides: dict[str, str], capability: str) -> str | None:
    """The operator's override: an exact capability beats a `prefix.*` one; longest prefix wins."""
    exact = overrides.get(capability)
    if exact is not None:
        return exact
    best, best_len = None, -1
    for pattern, mode in overrides.items():
        if (
            pattern.endswith(".*")
            and capability_matches(pattern, capability)
            and (len(pattern) > best_len)
        ):
            best, best_len = mode, len(pattern)
    return best


@dataclass(frozen=True)
class _Rule:
    capability: str | None
    resource: Glob | None
    risk_classes: frozenset[str]
    has_attributes: bool
    mode: str


@dataclass(frozen=True)
class FailModeTable:
    """The server's table as cached; built by `parse`, which drops anything malformed."""

    policy_version: int
    rules: tuple[_Rule, ...]
    defaults: dict[str, str]

    @classmethod
    def parse(cls, data: Any) -> "FailModeTable | None":
        if not isinstance(data, dict):
            return None
        version = data.get("policy_version")
        listed = data.get("rules")
        if not isinstance(version, int) or isinstance(version, bool):
            return None
        if not isinstance(listed, list):
            return None
        rules: list[_Rule] = []
        for item in listed[:MAX_TABLE_RULES]:
            rule = _parse_rule(item)
            if rule is not None:
                rules.append(rule)
        raw_defaults = data.get("defaults")
        defaults: dict[str, str] = {}
        if isinstance(raw_defaults, dict):
            for key, mode in raw_defaults.items():
                if isinstance(key, str) and mode in MODES and len(key) <= 16:
                    defaults[key] = mode
        return cls(version, tuple(rules), defaults)

    def mode_for(self, capability: str, resource: str, risk_class: str | None) -> str:
        for rule in self.rules:
            if rule.capability is not None and not capability_matches(rule.capability, capability):
                continue
            if rule.resource is not None and not rule.resource.matches(resource):
                continue
            if rule.risk_classes and risk_class not in rule.risk_classes:
                continue
            if rule.has_attributes and rule.mode != CLOSED:
                continue  # attribute conditions are unknown here: believe only the strict answer
            return rule.mode
        return self.defaults.get(risk_class or "unclassified") or default_mode(risk_class)


def _parse_rule(item: Any) -> _Rule | None:
    if not isinstance(item, dict):
        return None
    mode = item.get("on_error")
    capability = item.get("capability")
    resource = item.get("resource")
    classes = item.get("risk_classes")
    if mode not in MODES:
        return None
    if capability is not None and (not isinstance(capability, str) or len(capability) > 128):
        return None
    if resource is not None and (not isinstance(resource, str) or len(resource) > _MAX_PATTERN):
        return None
    if not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
        return None
    return _Rule(
        capability,
        None if resource is None else compile_glob(resource),
        frozenset(classes),
        item.get("has_attribute_conditions") is True,
        mode,
    )
