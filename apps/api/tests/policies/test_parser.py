"""The policy DSL reader: valid documents, every error code with its line and column, and hostile
input (ADR-140). Errors never echo a value."""

import time

import pytest

from abb_api.policies.model import (
    DEFAULT_EXPIRES_SECONDS,
    MAX_DOCUMENT_BYTES,
    MAX_RULES,
    DecisionKind,
    ExpiryAction,
    FailMode,
)
from abb_api.policies.parser import ISSUE_CODES, parse_policy

SECTION_151 = """\
version: 1
policies:
  - id: deny-env-read
    description: Do not allow agents to read dotenv secret files.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny

  - id: approve-production-merge
    when:
      capability: github.merge
      attributes:
        environment: production
    decision: require_approval
    approver_role: security
    expires_in: 15m

  - id: deny-destructive-db
    when:
      capability: database.execute
      risk_class: R4
    decision: deny
"""


def issues_of(text: str) -> list[tuple[int, int, str]]:
    return [(i.line, i.column, i.code) for i in parse_policy(text).issues]


def test_the_section_151_example_is_valid() -> None:
    result = parse_policy(SECTION_151)
    assert result.issues == () and result.policy is not None and result.ok
    policy = result.policy
    assert [r.id for r in policy.rules] == [
        "deny-env-read",
        "approve-production-merge",
        "deny-destructive-db",
    ]
    assert policy.default is DecisionKind.ALLOW
    merge = policy.rules[1]
    assert merge.decision is DecisionKind.REQUIRE_APPROVAL
    assert merge.approver_role == "SECURITY"
    assert merge.expires_in_seconds == 900
    assert merge.on_expiry is ExpiryAction.DENY  # the default
    assert merge.when.attributes == (("environment", "production"),)
    assert policy.rules[2].when.risk_classes == ("R4",)


def test_every_optional_field_and_the_defaults() -> None:
    result = parse_policy(
        """\
version: 1
default: deny
policies:
  - id: r1
    description: one
    when: {capability: "shell.*", risk_class: [R3, R4, R3]}
    decision: require_approval
    expires_in: 2d
    on_expiry: allow
    on_error: closed
    allow_self_approval: true
    approver_role: Developer
  - id: r2
    when: {resource: "src/**", attributes: {n: 3, ok: true, ratio: 0.5, name: "yes"}}
    decision: allow
    on_error: open
  - id: r3
    when: {capability: x.y}
    decision: require_approval
"""
    )
    assert result.issues == ()
    assert result.policy is not None
    policy = result.policy
    assert policy.default is DecisionKind.DENY
    r1, r2, r3 = policy.rules
    assert r1.when.risk_classes == ("R3", "R4")
    assert (r1.expires_in_seconds, r1.on_expiry, r1.on_error) == (
        172800,
        ExpiryAction.ALLOW,
        FailMode.CLOSED,
    )
    assert r1.allow_self_approval is True and r1.approver_role == "DEVELOPER"
    assert r2.on_error is FailMode.OPEN
    assert r2.when.attributes == (("n", 3), ("name", "yes"), ("ok", True), ("ratio", 0.5))
    assert (r3.approver_role, r3.expires_in_seconds, r3.on_expiry, r3.allow_self_approval) == (
        None,
        DEFAULT_EXPIRES_SECONDS,
        ExpiryAction.DENY,
        False,
    )


def test_an_empty_rule_list_and_a_default_only_policy_are_valid() -> None:
    for text in (
        "version: 1\n",
        "version: 1\npolicies:\n",
        "version: 1\npolicies: []\ndefault: deny\n",
    ):
        assert parse_policy(text).ok, text


def test_yaml_one_one_surprises_stay_text() -> None:
    result = parse_policy(
        "version: 1\npolicies:\n  - id: a\n    when:\n"
        "      attributes: {a: no, b: on, c: 0777, d: '1:30'}\n"
        "    decision: deny\n"
    )
    assert result.policy is not None
    assert result.policy.rules[0].when.attributes == (
        ("a", "no"),
        ("b", "on"),
        ("c", 777),  # an integer written with a leading zero is decimal, not octal
        ("d", "1:30"),
    )


def rule(extra: str, *, when: str = "capability: a.b", decision: str = "deny") -> str:
    return (
        f"version: 1\npolicies:\n  - id: r\n    when:\n      {when}\n"
        f"    decision: {decision}\n{extra}"
    )


# (document, expected line, expected column, expected code). Line and column are 1-based and
# point at the offending node; the document is small enough to count by eye.
ERRORS = [
    ("", 1, 1, "empty_document"),
    ("a: [", 1, 5, "syntax_error"),
    ("version: 1\n---\nversion: 1\n", 2, 1, "multiple_documents"),
    ("version: 1\nx: &a 1\n", 2, 4, "anchor_not_allowed"),
    ("version: 1\npolicies: *a\n", 2, 11, "alias_not_allowed"),
    ("version: !!str 1\n", 1, 10, "tag_not_allowed"),
    ("version: 1\n<<: {a: b}\n", 2, 1, "merge_key_not_allowed"),
    ("- a\n", 1, 1, "wrong_type"),
    ("version: 1\nbogus: 1\n", 2, 1, "unknown_key"),
    ("version: 1\nversion: 1\n", 2, 1, "duplicate_key"),
    ("policies: []\n", 1, 1, "missing_key"),
    ("version: 2\n", 1, 10, "invalid_version"),
    ("version: true\n", 1, 10, "invalid_version"),
    ("version: 1\ndefault: maybe\n", 2, 10, "invalid_default"),
    ("version: 1\ndefault: require_approval\n", 2, 10, "invalid_default"),
    ("version: 1\npolicies: 3\n", 2, 11, "wrong_type"),
    ("version: 1\npolicies: [x]\n", 2, 12, "wrong_type"),
    (rule("").replace("id: r", "id: Bad_ID"), 3, 9, "invalid_rule_id"),
    (
        "version: 1\npolicies:\n  - id: r\n    when: {capability: a}\n    decision: deny\n"
        "  - id: r\n    when: {capability: a}\n    decision: deny\n",
        6,
        9,
        "duplicate_rule_id",
    ),
    (rule("    description: a\\nb\n").replace("a\\nb", '"a\\nb"'), 7, 18, "invalid_description"),
    (rule("").replace("when:\n      capability: a.b", "when: {}"), 4, 11, "empty_when"),
    (rule("").replace("when:\n      capability: a.b", "when:"), 4, 10, "wrong_type"),
    (rule("", when="capability: 'a b'"), 5, 19, "invalid_capability"),
    (rule("", when="capability: '*'"), 5, 19, "invalid_capability"),
    (rule("", when="capability: a.*.b"), 5, 19, "invalid_capability"),
    (rule("", when=f"resource: {'x' * 257}"), 5, 17, "glob_too_long"),
    (rule("", when="risk_class: R9"), 5, 19, "invalid_risk_class"),
    (rule("", when="risk_class: []"), 5, 19, "invalid_risk_class"),
    (rule("", when="attributes: {a: [1]}"), 5, 23, "invalid_attribute"),
    (rule("", when="attributes: {'a b': 1}"), 5, 20, "invalid_attribute"),
    (
        rule("", when="attributes: {" + ", ".join(f"k{i}: 1" for i in range(9)) + "}"),
        5,
        19,
        "too_many_attributes",
    ),
    (rule("", decision="nope"), 6, 15, "invalid_value"),
    (rule("    approver_role: owner\n"), 7, 5, "field_not_allowed"),
    (rule("    expires_in: 15m\n"), 7, 5, "field_not_allowed"),
    (rule("    expires_in: soon\n", decision="require_approval"), 7, 17, "invalid_duration"),
    (rule("    expires_in: 30s\n", decision="require_approval"), 7, 17, "invalid_duration"),
    (rule("    expires_in: 0m\n", decision="require_approval"), 7, 17, "duration_out_of_range"),
    (rule("    expires_in: 8d\n", decision="require_approval"), 7, 17, "duration_out_of_range"),
    (rule("    approver_role: viewer\n", decision="require_approval"), 7, 20, "invalid_role"),
    (rule("    approver_role: billing\n", decision="require_approval"), 7, 20, "invalid_role"),
    (rule("    on_expiry: maybe\n", decision="require_approval"), 7, 16, "invalid_value"),
    (rule("    on_error: maybe\n"), 7, 15, "invalid_value"),
    (rule("    allow_self_approval: 1\n", decision="require_approval"), 7, 26, "wrong_type"),
    (rule("").replace("    decision: deny\n", ""), 3, 5, "missing_key"),
    (rule("    zzz: 1\n", when="capability: a"), 7, 5, "unknown_key"),
    ("version: 1\r\npolicies:" + chr(0x2028), 2, 10, "invalid_character"),
    ("version: 1\n\x00", 2, 1, "invalid_character"),
    ("version: 1\n﻿policies: []\n", 2, 1, "invalid_character"),
]  # fmt: skip


@pytest.mark.parametrize(("text", "line", "column", "code"), ERRORS)
def test_each_error_has_its_code_and_position(text: str, line: int, column: int, code: str) -> None:
    result = parse_policy(text)
    assert result.policy is None
    first = result.issues[0]
    assert (first.line, first.column, first.code) == (line, column, code), result.issues


def test_the_table_covers_every_issue_code() -> None:
    covered = {code for _, _, _, code in ERRORS}
    # these three need generated input and have their own tests below
    covered |= {
        "document_too_large",
        "too_deep",
        "too_many_nodes",
        "too_many_rules",
        "too_many_errors",
    }
    assert covered == ISSUE_CODES


def test_unterminated_flow_and_bad_indentation_are_syntax_errors_with_a_position() -> None:
    for text in ("version: 1\npolicies: [\n", "version: 1\n  bad: indent\n", "a: 'unterminated\n"):
        result = parse_policy(text)
        assert [i.code for i in result.issues] == ["syntax_error"], text
        assert result.issues[0].line >= 1 and result.issues[0].column >= 1


def test_several_problems_are_all_reported_in_one_pass() -> None:
    result = parse_policy(
        "version: 1\npolicies:\n  - id: Bad\n    when: {}\n    decision: x\n  - id: ok\n"
        "    when: {capability: a}\n    decision: deny\n    zzz: 1\n"
    )
    assert {i.code for i in result.issues} == {
        "unknown_key",
        "invalid_rule_id",
        "empty_when",
        "invalid_value",
    }


def test_the_error_list_is_capped() -> None:
    body = "".join(
        f"  - id: BAD{i}\n    when: {{capability: a}}\n    decision: deny\n" for i in range(80)
    )
    issues = parse_policy("version: 1\npolicies:\n" + body).issues
    assert len(issues) == 51 and issues[-1].code == "too_many_errors"


# ----------------------------------------------------------------------------- hostile input


def test_an_oversize_document_is_refused_before_parsing() -> None:
    started = time.perf_counter()
    result = parse_policy("version: 1\n# " + "x" * MAX_DOCUMENT_BYTES + "\n")
    assert [i.code for i in result.issues] == ["document_too_large"]
    assert time.perf_counter() - started < 0.1
    # a multi-byte document is measured in bytes, not characters
    assert [i.code for i in parse_policy("é" * (MAX_DOCUMENT_BYTES // 2 + 1)).issues] == [
        "document_too_large"
    ]


def test_too_many_rules() -> None:
    body = "".join(
        f"  - id: r{i}\n    when: {{capability: a}}\n    decision: deny\n"
        for i in range(MAX_RULES + 1)
    )
    assert issues_of("version: 1\npolicies:\n" + body)[0][2] == "too_many_rules"
    assert parse_policy(
        "version: 1\npolicies:\n"
        + "".join(
            f"  - id: r{i}\n    when: {{capability: a}}\n    decision: deny\n"
            for i in range(MAX_RULES)
        )
    ).ok


def test_deep_nesting_is_refused_without_recursion() -> None:
    for text in ("[" * 5000, "a: " + "[" * 5000, "- " * 40 + "x"):
        started = time.perf_counter()
        codes = [i.code for i in parse_policy(text).issues]
        assert codes and codes[0] in {"too_deep", "syntax_error"}, codes
        assert time.perf_counter() - started < 0.5
    assert [i.code for i in parse_policy("[" * 50 + "]" * 50).issues] == ["too_deep"]


def test_too_many_nodes() -> None:
    assert issues_of("version: 1\nx: [" + ",".join(["1"] * 6000) + "]\n")[0][2] == "too_many_nodes"


def test_billion_laughs_is_refused_at_the_first_alias() -> None:
    bomb = (
        "a: &a [x,x,x,x,x,x,x,x,x]\nb: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
        "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\nd: [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n"
    )
    started = time.perf_counter()
    result = parse_policy(bomb)
    assert [i.code for i in result.issues] == ["anchor_not_allowed"]
    assert time.perf_counter() - started < 0.1


@pytest.mark.parametrize(
    "text",
    [
        "version: !!python/object/apply:os.system ['id']\n",
        "version: 1\npolicies: !!python/name:os.system\n",
        "version: 1\nx: !<tag:yaml.org,2002:binary> aGk=\n",
        "version: 1\nx: !custom {a: 1}\n",
    ],
)
def test_tags_never_construct_anything(text: str) -> None:
    assert [i.code for i in parse_policy(text).issues] == ["tag_not_allowed"]


@pytest.mark.parametrize("ch", ["\x00", "\x07", "\x1b", "\x7f", "\u0085", "​", "‮", "⁦", "\ud800"])
def test_control_format_and_unpaired_surrogate_characters_are_refused(ch: str) -> None:
    result = parse_policy(f"version: 1\ndefault: allow\n# note {ch} here\n")
    assert [i.code for i in result.issues] == ["invalid_character"]
    assert result.issues[0].line in (1, 3)


def test_unicode_text_is_fine_in_descriptions_and_resources() -> None:
    result = parse_policy(
        "version: 1\npolicies:\n  - id: uni\n"
        '    description: "Zugriff verweigern \U0001f510 日本語"\n'
        "    when: {resource: 'données/**'}\n    decision: deny\n"
    )
    assert result.ok


def test_errors_never_echo_a_value() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE-hunter2"
    documents = [
        f"version: {secret}\n",
        f"version: 1\ndefault: {secret}\n",
        f"version: 1\npolicies:\n  - id: {secret}\n    when: {{capability: {secret}}}\n"
        f"    decision: {secret}\n",
        f"version: 1\npolicies:\n  - id: r\n"
        f"    when: {{risk_class: {secret}, attributes: {{a: [{secret}]}}}}\n"
        f"    decision: deny\n    expires_in: {secret}\n    approver_role: {secret}\n",
        f"{secret}\n",
        f"a: [{secret}\n",
        f'version: 1\npolicies:\n  - id: r\n    description: "{secret}\\n"\n'
        "    when: {capability: a}\n    decision: deny\n",
    ]
    for text in documents:
        for issue in parse_policy(text).issues:
            assert secret not in issue.message, issue
            assert "hunter2" not in issue.message


def test_a_key_name_is_echoed_but_cut_short() -> None:
    long_key = "k" * 500
    issue = parse_policy(f"version: 1\n{long_key}: 1\n").issues[0]
    assert issue.code == "unknown_key" and len(issue.message) < 100


def test_parsing_is_deterministic() -> None:
    assert parse_policy(SECTION_151).policy == parse_policy(SECTION_151).policy
