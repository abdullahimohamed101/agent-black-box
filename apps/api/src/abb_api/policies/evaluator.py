"""The pure policy evaluator (spec §88, ADR-140).

`evaluate(policy, intent)` is a function of its two arguments only: no clock, no I/O, no state.
Rules are tried in listed order and the first rule whose every condition holds decides; no match
gives the policy default. Case rules: capabilities, resources, attribute names and string values
are all compared case sensitively. The reason is fixed text built from the matching rule's own
description (or its id), so a decision's reason can never carry content from the intent.
"""

from abb_api.policies.model import (
    EMPTY_POLICY,
    AttributeValue,
    Decision,
    DecisionKind,
    Intent,
    Policy,
    Rule,
)

DEFAULT_REASONS = {
    DecisionKind.ALLOW: "No rule matched; the policy default is allow.",
    DecisionKind.DENY: "No rule matched; the policy default is deny.",
}
NO_POLICY_REASON = "No policy is published; actions are allowed."


def capability_matches(pattern: str, capability: str) -> bool:
    """Exact, or `prefix.*`: any capability below `prefix.` (at least one more segment)."""
    if pattern.endswith(".*"):
        prefix = pattern[:-1]  # keeps the dot
        return len(capability) > len(prefix) and capability.startswith(prefix)
    return pattern == capability


def _same(expected: AttributeValue, actual: AttributeValue | None) -> bool:
    if actual is None or isinstance(expected, bool) != isinstance(actual, bool):
        return False  # True is not 1 here
    if isinstance(expected, str) or isinstance(actual, str):
        return isinstance(expected, str) and isinstance(actual, str) and expected == actual
    return expected == actual


def rule_matches(rule: Rule, intent: Intent) -> bool:
    when = rule.when
    if when.capability is not None and not capability_matches(when.capability, intent.capability):
        return False
    if when.risk_classes and intent.risk_class not in when.risk_classes:
        return False
    if when.resource is not None and not when.resource.matches(intent.resource):
        return False
    return all(_same(value, intent.attributes.get(name)) for name, value in when.attributes)


def evaluate(policy: Policy, intent: Intent) -> Decision:
    for rule in policy.rules:
        if rule_matches(rule, intent):
            return Decision(
                rule.decision,
                rule.id,
                policy.version,
                rule.description or f"Matched rule {rule.id}.",
            )
    reason = NO_POLICY_REASON if policy == EMPTY_POLICY else DEFAULT_REASONS[policy.default]
    return Decision(policy.default, None, policy.version, reason)
