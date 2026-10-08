"""SDK redaction pipeline (spec §67.5): rules, secret detection, callback, payload modes."""

import json
from typing import Any

import pytest

from blackbox import PayloadMode
from blackbox.redaction import Redactor
from blackbox.stats import Stats
from tests.helpers import events_of, offline

SECRETS = {
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "github_token": "ghp_" + "a1B2c3D4e5F6" * 3,
    "slack_token": "xoxb-1234567890-abcdefghij",
    "api_key": "sk-ant-api03-" + "abcDEF123" * 4,
    "abb_api_key": "abb_live_01HXYZ12345.supersecretvalue",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gF",
}


@pytest.mark.parametrize("kind", SECRETS)
def test_secret_shapes_are_replaced_in_strings(kind: str) -> None:
    out = Redactor().redact_string(f"calling with {SECRETS[kind]} now")
    assert SECRETS[kind] not in out and f"[REDACTED:{kind}]" in out


def test_other_secret_forms() -> None:
    r = Redactor()
    assert "hunter22" not in r.redact_string("login password=hunter22 ok")
    assert (
        r.redact_string("login password=hunter22 ok") == "login password=[REDACTED:credential] ok"
    )
    assert "s3cr3tpw" not in r.redact_string("postgres://admin:s3cr3tpw@db.internal/app")
    assert "Bearer [REDACTED:bearer]" in r.redact_string(
        "Authorization: Bearer abcdef0123456789abcdef"
    )
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert r.redact_string(f"key {pem} end") == "key [REDACTED:private_key] end"
    assert (
        r.redact_string("truncated -----BEGIN PRIVATE KEY-----\nMIIE")
        == "truncated [REDACTED:private_key]"
    )


def test_plain_text_is_untouched() -> None:
    text = "Refactor the parser, then run the tests (all 42 of them)."
    assert Redactor().redact_string(text) == text


def test_denied_keys_replace_values_but_not_numbers() -> None:
    r = Redactor()
    value = {
        "user": "ann",
        "Password": "p4ss",
        "headers": {"Authorization": "Basic zzz"},
        "max_tokens": 100,
        "items": [{"api-key": "k"}],
        "cookie": ["a", "b"],
    }
    out = r.redact_value(value)
    assert out["user"] == "ann" and out["max_tokens"] == 100
    assert out["Password"] == "[REDACTED:password]"
    assert out["headers"]["Authorization"] == "[REDACTED:authorization]"
    assert out["items"][0]["api-key"] == "[REDACTED:api_key]"
    assert out["cookie"] == "[REDACTED:cookie]"  # the whole value goes, whatever its shape


def test_allow_keys_exempt_a_key_from_the_deny_rule_only() -> None:
    r = Redactor(allow_keys=["token_count"], deny_keys=["token"])
    assert r.redact_value({"token_count": "12"}) == {"token_count": "12"}
    assert r.redact_value({"note": "password=abc12345"}) == {
        "note": "password=[REDACTED:credential]"
    }


def test_non_json_values_and_deep_nesting_are_neutralised() -> None:
    r = Redactor()
    assert r.redact_value({"o": object(), "s": {1, 2}}) == {"o": "<object>", "s": "<set>"}
    deep: Any = "leaf"
    for _ in range(40):
        deep = [deep]
    assert "[TRUNCATED]" in json.dumps(r.redact_value(deep))


def test_redaction_is_deterministic_and_idempotent() -> None:
    value = {
        "a": f"x {SECRETS['github_token']} y",
        "password": "p",
        "n": [1, "AKIAIOSFODNN7EXAMPLE"],
    }
    once = Redactor().redact_value(value)
    assert once == Redactor().redact_value(value)
    assert Redactor().redact_value(once) == once


def event(**fields: Any) -> dict[str, Any]:
    return {"event_type": "custom.x", "attributes": {}, **fields}


def test_payload_modes() -> None:
    payload = {"prompt": "hi", "password": "p"}
    kept = Redactor(payload_mode=PayloadMode.FULL).apply(event(payload=payload))
    assert kept is not None and kept["payload"] == {
        "prompt": "hi",
        "password": "[REDACTED:password]",
    }
    for mode in (PayloadMode.METADATA_ONLY, PayloadMode.DISABLED):
        stats = Stats()
        dropped = Redactor(payload_mode=mode, stats=stats).apply(
            event(payload=payload, payload_ref="artifact://x")
        )
        assert dropped is not None and "payload" not in dropped and "payload_ref" not in dropped
        assert stats["payloads_dropped"] == 1


def test_disabled_mode_also_drops_free_text() -> None:
    attrs = {
        "error.message": "boom",
        "error.type": "ValueError",
        "metadata.issue": "1",
        "tool.name": "x",
    }
    out = Redactor(payload_mode=PayloadMode.DISABLED).apply(event(attributes=dict(attrs)))
    assert out is not None and out["attributes"] == {"error.type": "ValueError", "tool.name": "x"}
    kept = Redactor(payload_mode=PayloadMode.METADATA_ONLY).apply(event(attributes=dict(attrs)))
    assert kept is not None and kept["attributes"]["error.message"] == "boom"


def test_attributes_and_tags_are_redacted_too() -> None:
    out = Redactor().apply(
        event(
            attributes={
                "shell.command": "curl -H 'Authorization: Bearer abcdef0123456789abcd'",
                "token": "t",
                "llm.input_tokens": 5,
            },
            tags=["ghp_" + "a1B2c3D4e5F6" * 3],
        )
    )
    assert out is not None
    assert "abcdef0123456789abcd" not in out["attributes"]["shell.command"]
    assert (
        out["attributes"]["token"] == "[REDACTED:token]"
        and out["attributes"]["llm.input_tokens"] == 5
    )
    assert out["tags"] == ["[REDACTED:github_token]"]


def test_callback_runs_after_builtin_rules_and_can_drop() -> None:
    seen: list[Any] = []

    def callback(e: dict[str, Any]) -> dict[str, Any] | None:
        seen.append(e["attributes"].get("note"))
        if e["attributes"].get("drop"):
            return None
        e["attributes"]["note"] = e["attributes"]["note"].replace("ann", "<name>")
        return e

    r = Redactor(callback=callback)
    out = r.apply(event(attributes={"note": "ann password=abc12345"}))
    assert seen == ["ann password=[REDACTED:credential]"]
    assert out is not None and out["attributes"]["note"] == "<name> password=[REDACTED:credential]"
    assert r.apply(event(attributes={"note": "x", "drop": True})) is None


def test_a_failing_callback_fails_closed() -> None:
    def boom(e: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("bug")

    stats = Stats()
    r = Redactor(callback=boom, stats=stats, payload_mode=PayloadMode.FULL)
    leaky = event(attributes={"note": "private", "span.name": "plan"}, payload={"a": 1})
    assert r.apply(dict(leaky)) is None  # ordinary events are dropped
    p0 = r.apply(dict(leaky), p0=True)  # lifecycle events survive without free text or payload
    assert p0 is not None and p0["attributes"] == {"span.name": "plan"} and "payload" not in p0
    assert stats["dropped_redaction_error"] == 2


def test_end_to_end_secrets_never_reach_the_queue() -> None:
    bb = offline(payload_mode="full")
    secret = SECRETS["github_token"]
    with bb.run("r", metadata={"token_hint": secret}) as run:
        with run.span("s", attributes={"url": f"https://x:{secret}@h/"}) as span:
            span.set_payload({"authorization": "Bearer abcdef0123456789abcdef", "text": secret})
        try:
            with run.span("f"):
                raise RuntimeError(f"failed with {secret}")
        except RuntimeError:
            pass
    assert secret not in json.dumps(events_of(bb))
    assert bb.stats()["redactions"] >= 4


def test_a_huge_wide_value_is_cut_after_a_node_budget() -> None:
    wide = {f"k{i}": "v" for i in range(20_000)}
    out = Redactor().redact_value(wide)
    assert list(out.values()).count("[TRUNCATED]") > 10_000 and out["k0"] == "v"


# -- artifact-text redaction: shell-shaped secrets (ADR-031) ---------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (
            "echo AKIAABCDEFGHIJKLMNOP",
            "AKIAABCDEFGHIJKLMNOP",
        ),  # letters only: no digit, no punctuation
        ("curl -u admin:hunter22 https://example.com", "hunter22"),
        ("curl --user admin:hunter22 https://example.com", "hunter22"),
        ("curl -H 'Authorization: Basic dXNlcjpwYXNzd29yZA==' x", "dXNlcjpwYXNzd29yZA"),
        ("curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx' x", "abcdefghijklmnopqrstuvwx"),
        ("Authorization: token ghx_notapatternbutsecret", "ghx_notapatternbutsecret"),
        (
            "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY",
            "wJalrXUtnFEMIK7MDENGbPxRfiCY",
        ),
        ("DB_PASSWORD='p@ss w0rd' ./run", "p@ss w0rd"),
        ("GITHUB_TOKEN=notapattern123456 make", "notapattern123456"),
        ("MY_API_KEY: s3cretvalue99", "s3cretvalue99"),
        ("tool --password hunter2xyz run", "hunter2xyz"),
        ("tool --token=abcd1234efgh run", "abcd1234efgh"),
        ("tool --api-key abcd1234efgh run", "abcd1234efgh"),
        ("git clone https://deploy:s3cr3tpw@github.com/o/r.git", "s3cr3tpw"),
        (
            "git clone https://ghx_abcdefghijklmnopqrstuv@github.com/o/r.git",
            "ghx_abcdefghijklmnopqrstuv",
        ),
        ("tok \x1b[31mghp_\x1b[0m" + "a" * 36, "a" * 36),  # ANSI between the prefix and the body
    ],
)
def test_shell_shaped_secrets_are_redacted_from_artifact_text(text: str, secret: str) -> None:
    out = Redactor().redact_text(text)
    assert secret not in out and "[REDACTED" in out


def test_ordinary_text_survives_redaction() -> None:
    plain = (
        "KeyError: 'missing'\nAssertionError: 1 != 2\nRan 5 tests in 0.002s\nprocessed 1234 items"
    )
    assert Redactor().redact_text(plain) == plain
