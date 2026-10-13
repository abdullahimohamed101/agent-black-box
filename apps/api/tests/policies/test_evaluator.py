"""The evaluator: golden policies, properties (order, first match, default, determinism) and the
guarantee that a reason never carries intent content (ADR-140)."""

import random
import time
from dataclasses import replace

import pytest

from abb_api.policies.evaluator import capability_matches, evaluate
from abb_api.policies.model import (
    EMPTY_POLICY,
    AttributeValue,
    Decision,
    DecisionKind,
    Intent,
    Policy,
)
from abb_api.policies.parser import parse_policy
from tests.policies.test_parser import SECTION_151

ALLOW, DENY, ASK = DecisionKind.ALLOW, DecisionKind.DENY, DecisionKind.REQUIRE_APPROVAL


def compiled(text: str, version: int = 3) -> Policy:
    result = parse_policy(text)
    assert result.policy is not None, result.issues
    return result.policy.with_version(version)


GOLDEN = [
    # (intent, decision, rule id) against the §151 example at version 3
    (Intent("filesystem.read", ".env"), DENY, "deny-env-read"),
    (Intent("filesystem.read", "app/.env.production"), DENY, "deny-env-read"),
    (Intent("filesystem.read", "app/env.py"), ALLOW, None),
    (Intent("filesystem.write", ".env"), ALLOW, None),  # a different capability
    (Intent("github.merge", "repo:o/p#pr-1", "R3", {"environment": "production"}), ASK,
     "approve-production-merge"),
    (Intent("github.merge", "repo:o/p#pr-1", "R3", {"environment": "staging"}), ALLOW, None),
    (Intent("github.merge", "repo:o/p#pr-1", "R3"), ALLOW, None),  # attribute absent
    (Intent("github.merge", "x", None, {"environment": "Production"}), ALLOW, None),  # case
    (Intent("database.execute", "db", "R4"), DENY, "deny-destructive-db"),
    (Intent("database.execute", "db", "R3"), ALLOW, None),
    (Intent("database.execute", "db", None), ALLOW, None),
]  # fmt: skip


@pytest.mark.parametrize(("intent", "kind", "rule_id"), GOLDEN)
def test_section_151_golden(intent: Intent, kind: DecisionKind, rule_id: str | None) -> None:
    decision = evaluate(compiled(SECTION_151), intent)
    assert (decision.decision, decision.rule_id, decision.policy_version) == (kind, rule_id, 3)


def test_reasons_come_from_the_rule_or_a_fixed_sentence() -> None:
    policy = compiled(SECTION_151)
    assert evaluate(policy, Intent("filesystem.read", ".env")).reason == (
        "Do not allow agents to read dotenv secret files."
    )
    assert evaluate(policy, Intent("database.execute", "x", "R4")).reason == (
        "Matched rule deny-destructive-db."
    )
    assert evaluate(policy, Intent("x.y")).reason == "No rule matched; the policy default is allow."
    assert evaluate(EMPTY_POLICY, Intent("x.y")) == Decision(
        ALLOW, None, 0, "No policy is published; actions are allowed."
    )
    deny_all = compiled("version: 1\ndefault: deny\n")
    assert (
        evaluate(deny_all, Intent("x.y")).reason == "No rule matched; the policy default is deny."
    )


def test_a_reason_never_contains_intent_content() -> None:
    sentinel = "SENTINEL-4f9c-secret-path"
    policy = compiled(SECTION_151)
    intents = [
        Intent("filesystem.read", f"{sentinel}/.env", "R0", {"environment": sentinel}),
        Intent(f"x.{sentinel}", sentinel, "R4", {sentinel: sentinel}),
        Intent("database.execute", sentinel, "R4", {"environment": sentinel}),
        Intent("github.merge", sentinel, None, {"environment": "production"}),
    ]
    for intent in intents:
        decision = evaluate(policy, intent)
        assert sentinel not in decision.reason
        assert sentinel not in repr(decision)


@pytest.mark.parametrize(
    ("pattern", "capability", "expected"),
    [
        ("filesystem.read", "filesystem.read", True),
        ("filesystem.read", "filesystem.read.extra", False),
        ("filesystem.read", "Filesystem.read", False),  # case sensitive
        ("filesystem.*", "filesystem.read", True),
        ("filesystem.*", "filesystem.read.deep", True),
        ("filesystem.*", "filesystem", False),
        ("filesystem.*", "filesystem.", False),
        ("filesystem.*", "filesystems.read", False),
        ("tool.*", "tool.github-search", True),
    ],
)
def test_capability_matching(pattern: str, capability: str, expected: bool) -> None:
    assert capability_matches(pattern, capability) is expected


def test_attribute_matching_is_exact_and_typed() -> None:
    policy = compiled(
        "version: 1\npolicies:\n  - id: a\n    when: {attributes: {n: 1, flag: true, s: '1'}}\n"
        "    decision: deny\n"
    )
    hit = Intent("c.d", attributes={"n": 1, "flag": True, "s": "1"})
    assert evaluate(policy, hit).decision is DENY
    assert (
        evaluate(policy, Intent("c.d", attributes={"n": 1.0, "flag": True, "s": "1"})).decision
        is DENY
    )
    wrong: list[dict[str, AttributeValue]] = [
        {"n": True, "flag": True, "s": "1"},  # True is not 1
        {"n": 1, "flag": 1, "s": "1"},
        {"n": 1, "flag": True, "s": 1},  # "1" is not 1
        {"n": 1, "flag": True},
        {"n": "1", "flag": True, "s": "1"},
    ]
    for attrs in wrong:
        assert evaluate(policy, Intent("c.d", attributes=attrs)).decision is ALLOW, attrs


def test_risk_class_list_membership() -> None:
    policy = compiled(
        "version: 1\npolicies:\n  - id: a\n    when: {risk_class: [R3, R4]}\n    decision: deny\n"
    )
    assert [
        evaluate(policy, Intent("c.d", risk_class=r)).decision for r in ("R2", "R3", "R4", None)
    ] == [
        ALLOW,
        DENY,
        DENY,
        ALLOW,
    ]


def test_every_condition_must_hold() -> None:
    policy = compiled(
        "version: 1\npolicies:\n  - id: a\n    when:\n      capability: shell.execute\n"
        "      resource: 'git'\n      risk_class: R3\n      attributes: {category: network}\n"
        "    decision: require_approval\n"
    )
    full = Intent("shell.execute", "git", "R3", {"category": "network"})
    assert evaluate(policy, full).decision is ASK
    for changed in (
        replace(full, capability="shell.other"),
        replace(full, resource="git2"),
        replace(full, risk_class="R2"),
        replace(full, attributes={"category": "local"}),
    ):
        assert evaluate(policy, changed).decision is ALLOW


def test_an_explicit_allow_above_a_broader_deny_is_the_exception() -> None:
    text = """\
version: 1
policies:
  - id: allow-example
    when: {capability: filesystem.read, resource: "**/.env.example"}
    decision: allow
  - id: deny-env
    when: {capability: filesystem.read, resource: "**/.env*"}
    decision: deny
"""
    policy = compiled(text)
    assert (
        evaluate(policy, Intent("filesystem.read", "app/.env.example")).rule_id == "allow-example"
    )
    assert evaluate(policy, Intent("filesystem.read", "app/.env")).rule_id == "deny-env"
    reversed_policy = Policy(tuple(reversed(policy.rules)), policy.default, policy.version)
    assert (
        evaluate(reversed_policy, Intent("filesystem.read", "app/.env.example")).rule_id
        == "deny-env"
    )


# ----------------------------------------------------------------------------- properties


def random_rules_text(rng: random.Random, count: int) -> str:
    caps = ["a.b", "a.c", "a.*", "b.*", "c.d"]
    resources = ["**", "x/*", "x/**", "y", "**/z", "*"]
    out = ["version: 1", f"default: {rng.choice(['allow', 'deny'])}", "policies:"]
    for i in range(count):
        out += [
            f"  - id: r{i}",
            "    when:",
            f"      capability: {rng.choice(caps)}",
            f"      resource: '{rng.choice(resources)}'",
            f"    decision: {rng.choice(['allow', 'deny', 'require_approval'])}",
        ]
    return "\n".join(out) + "\n"


def random_intent(rng: random.Random) -> Intent:
    return Intent(
        rng.choice(["a.b", "a.c", "a.d", "b.q", "c.d", "z"]),
        rng.choice(["x/1", "x/a/b", "y", "q/z", "z", "", "w"]),
    )


def test_first_match_wins_and_default_applies_otherwise() -> None:
    rng = random.Random(7)
    for _ in range(300):
        policy = compiled(random_rules_text(rng, rng.randint(0, 8)))
        for _ in range(10):
            intent = random_intent(rng)
            decision = evaluate(policy, intent)
            matching = [r for r in policy.rules if evaluate(Policy((r,), DENY), intent).rule_id]
            if matching:
                assert (
                    decision.rule_id == matching[0].id and decision.decision is matching[0].decision
                )
            else:
                assert (decision.rule_id, decision.decision) == (None, policy.default)


def test_the_result_is_deterministic_and_independent_of_other_state() -> None:
    rng = random.Random(11)
    for _ in range(100):
        text = random_rules_text(rng, 6)
        intent = random_intent(rng)
        first = evaluate(compiled(text), intent)
        random_intent(rng)  # unrelated work in between changes nothing
        assert evaluate(compiled(text), intent) == first == evaluate(compiled(text), intent)


def test_order_matters_exactly_when_two_matching_rules_disagree() -> None:
    rng = random.Random(21)
    disagreements = 0
    for _ in range(300):
        policy = compiled(random_rules_text(rng, 4))
        intent = random_intent(rng)
        flipped = Policy(tuple(reversed(policy.rules)), policy.default, policy.version)
        matches = [r for r in policy.rules if evaluate(Policy((r,), DENY), intent).rule_id]
        same = evaluate(policy, intent) == evaluate(flipped, intent)
        if len({r.decision for r in matches}) <= 1 and len(matches) <= 1:
            assert same
        if not same:
            disagreements += 1
            assert len(matches) >= 2
    assert disagreements > 0  # the generator really produces order-sensitive policies


def test_the_decision_carries_the_policy_version() -> None:
    policy = compiled(SECTION_151, version=9)
    assert evaluate(policy, Intent("x.y")).policy_version == 9
    assert evaluate(policy, Intent("filesystem.read", ".env")).policy_version == 9


def test_evaluating_a_large_policy_is_fast() -> None:
    rules = "".join(
        f"  - id: r{i}\n    when: {{capability: 'a.b', resource: 'dir{i}/**/*.py'}}\n"
        "    decision: deny\n"
        for i in range(200)
    )
    policy = compiled("version: 1\npolicies:\n" + rules)
    intent = Intent("a.b", "dir199/x/y/z.txt")
    started = time.perf_counter()
    for _ in range(200):
        assert evaluate(policy, intent).rule_id is None
    assert (time.perf_counter() - started) / 200 < 0.02  # a full miss over 200 rules, per call
