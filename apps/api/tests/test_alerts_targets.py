"""Early validation of destination targets (ADR-150): https only, no credentials, no non-public
literals, no internal names; every refusal is a fixed reason that never echoes the value."""

import pytest

from abb_api.alerts.targets import (
    TargetInvalid,
    mask_recipients,
    validate_recipients,
    validate_web_url,
)


def reason_of(url: object) -> str:
    with pytest.raises(TargetInvalid) as excinfo:
        validate_web_url(url)
    assert str(url) not in str(excinfo.value) or str(url) == ""
    return excinfo.value.reason


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXXXXXXXXX",
        "https://example.com/hook",
        "HTTPS://Example.COM:8443/a/b?x=1",
        "https://203.0.113.9/x".replace("203.0.113.9", "93.184.216.34"),
        "https://[2606:4700:4700::1111]/x",
    ],
)
def test_public_https_targets_are_accepted(url: str) -> None:
    assert validate_web_url(url).url.startswith("https://")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/hook", "not_https"),
        ("ftp://example.com/hook", "not_https"),
        ("//example.com/hook", "not_https"),
        ("https://user:pw@example.com/hook", "credentials_in_url"),
        ("https://user@example.com/hook", "credentials_in_url"),
        ("https://example.com@evil.example.org/", "credentials_in_url"),
        ("https://localhost/hook", "host_internal"),
        ("https://LOCALHOST./hook", "host_invalid"),
        ("https://app.localhost/hook", "host_internal"),
        ("https://printer.local/hook", "host_internal"),
        ("https://metadata.google.internal/computeMetadata", "host_internal"),
        ("https://intranet/hook", "host_internal"),
        ("https://127.0.0.1/hook", "ip_not_public"),
        ("https://10.0.0.5/hook", "ip_not_public"),
        ("https://172.16.0.1/hook", "ip_not_public"),
        ("https://192.168.1.1/hook", "ip_not_public"),
        ("https://169.254.169.254/latest/meta-data", "ip_not_public"),
        ("https://100.64.0.1/hook", "ip_not_public"),
        ("https://0.0.0.0/hook", "ip_not_public"),
        ("https://[::1]/hook", "ip_not_public"),
        ("https://[fe80::1]/hook", "ip_not_public"),
        ("https://[fd00::1]/hook", "ip_not_public"),
        ("https://[::ffff:127.0.0.1]/hook", "ip_not_public"),
        ("https://[::ffff:169.254.169.254]/hook", "ip_not_public"),
        ("https://[64:ff9b::7f00:1]/hook", "ip_not_public"),
        ("https://127.1/hook", "ip_literal_form"),
        ("https://2130706433/hook", "ip_literal_form"),
        ("https://0x7f000001/hook", "ip_literal_form"),
        ("https://0x7f.0.0.1/hook", "ip_literal_form"),
        ("https://example.com/hook#frag", "fragment_not_allowed"),
        ("https://example.com:0/hook", "port_invalid"),
        ("https://example.com:99999/hook", "url_malformed"),
        ("https://exa mple.com/hook", "url_malformed"),
        ("https://example.com\\@evil.example.org/", "url_malformed"),
        ("https://exam_ple.com/hook", "host_invalid"),
        ("https://-bad.example.com/hook", "host_invalid"),
        ("", "url_required"),
        ("   ", "url_required"),
        (None, "url_required"),
        (12, "url_required"),
        ("https://example.com/" + "a" * 2100, "url_too_long"),
    ],
)
def test_refusals_carry_a_fixed_reason(url: object, reason: str) -> None:
    assert reason_of(url) == reason


def test_a_url_is_normalised_and_masked() -> None:
    web = validate_web_url(
        "HTTPS://Hooks.Slack.com:443/services/T000/B000/SECRETSECRETSECRET1234?z=1"
    )
    assert web.url == "https://hooks.slack.com/services/T000/B000/SECRETSECRETSECRET1234?z=1"
    assert web.masked == "https://hooks.slack.com/…/1234"
    assert "SECRETSECRET" not in web.masked and "z=1" not in web.masked


def test_a_short_final_segment_is_not_shown_at_all() -> None:
    assert validate_web_url("https://example.com/hook").masked == "https://example.com/…"
    assert validate_web_url("https://example.com:8443/").masked == "https://example.com:8443"
    assert validate_web_url("https://[2606:4700:4700::1111]/x").masked.startswith(
        "https://[2606:4700:4700::1111]"
    )


def test_recipients_are_validated_normalised_and_masked() -> None:
    assert validate_recipients(["B@Example.com", "a@example.com", "a@EXAMPLE.com"]) == [
        "a@example.com",
        "B@example.com",
    ]
    assert mask_recipients(["alice@example.com", "bob@example.org"]) == (
        "a***@example.com, b***@example.org"
    )
    assert mask_recipients([f"u{i}@example.com" for i in range(5)]).endswith("(+2 more)")


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ([], "recipients_required"),
        (None, "recipients_required"),
        ("a@example.com", "recipients_required"),
        ([f"u{i}@example.com" for i in range(11)], "too_many_recipients"),
        (["not-an-address"], "recipient_invalid"),
        (["a@b"], "recipient_invalid"),
        (["a@@example.com"], "recipient_invalid"),
        (["a b@example.com"], "recipient_invalid"),
        ([".a@example.com"], "recipient_invalid"),
        (["a@example.123"], "recipient_invalid"),
        (["a@exa_mple.com"], "recipient_invalid"),
        ([5], "recipient_invalid"),
        (["a@example.com\r\nBcc: x@example.org"], "recipient_invalid"),
    ],
)
def test_bad_recipients_are_refused(value: object, reason: str) -> None:
    with pytest.raises(TargetInvalid) as excinfo:
        validate_recipients(value)
    assert excinfo.value.reason == reason
