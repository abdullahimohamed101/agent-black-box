"""The built-in notifiers (ADR-151): a signed webhook a receiver can verify, a Slack-shaped
message whose names cannot form links or mentions, and email whose names cannot inject headers."""

import email
import email.policy
import hashlib
import hmac
import json
import ssl
import uuid
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid

from abb_api.alerts import notifiers as n
from abb_api.alerts import outbound as o
from abb_api.alerts.notifiers import Alert, OpenDestination
from abb_api.core.config import Settings
from tests.alerts_receivers import (
    RESPONSE_SENTINEL,
    SmtpSink,
    TlsReceiver,
    make_certificate,
    server_context,
)
from tests.conftest import make_settings

ORIGIN = "https://abb.example.com"
SECRET = "whsec_TESTSIGNINGSECRET"


def payload(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "alert_id": from_uuid(IdKind.ALERT, uuid.uuid4()),
        "kind": "run_failed",
        "severity": "high",
        "title": "Run failed",
        "workspace_id": from_uuid(IdKind.WORKSPACE, uuid.uuid4()),
        "project": {"id": "prj_x", "slug": "alpha", "name": "Alpha"},
        "agent": {"slug": "coding-agent", "name": "Coding agent"},
        "rule": {"id": "rul_x", "name": "Failures"},
        "subject": {"run_id": "run_x"},
        "details": {"run_status": "FAILED"},
        "link_path": "/w/acme/projects/alpha/runs/run_x",
        "created_at": "2026-10-14T00:00:00+00:00",
    }
    base.update(changes)
    return base


def alert(**changes: Any) -> Alert:
    body = payload(**changes)
    return Alert(alert_id=body["alert_id"], payload=body, attempt=2, web_origin=ORIGIN)


def webhook_destination(url: str) -> OpenDestination:
    return OpenDestination(
        id=uuid.uuid4(), type="webhook", config={"url": url}, signing_secret=SECRET
    )


# ---------------------------------------------------------------- webhook


async def test_a_webhook_is_signed_over_timestamp_and_body_and_carries_the_alert_id() -> None:
    async with TlsReceiver() as receiver:
        notifier = n.WebhookNotifier(receiver.sender(), clock=lambda: 1_700_000_000.9)
        sent = alert()
        result = await notifier.send(webhook_destination(receiver.url()), sent)
    assert result == n.SendResult(True)
    (request,) = receiver.requests
    headers = request.headers
    assert headers["x-abb-alert-id"] == sent.alert_id == json.loads(request.body)["alert_id"]
    assert headers["x-abb-timestamp"] == "1700000000" and headers["x-abb-delivery-attempt"] == "2"
    expected = hmac.new(SECRET.encode(), b"1700000000." + request.body, hashlib.sha256).hexdigest()
    assert headers["x-abb-signature"] == f"v1={expected}"  # what a receiver recomputes and compares
    assert headers["content-type"] == "application/json"
    body = json.loads(request.body)
    assert body["link"] == f"{ORIGIN}/w/acme/projects/alpha/runs/run_x"
    assert body["link_path"] == "/w/acme/projects/alpha/runs/run_x"
    assert SECRET not in request.body.decode()


async def test_a_tampered_webhook_body_or_a_wrong_secret_does_not_verify() -> None:
    async with TlsReceiver() as receiver:
        await n.WebhookNotifier(receiver.sender(), clock=lambda: 5).send(
            webhook_destination(receiver.url()), alert()
        )
    (request,) = receiver.requests
    signature = request.headers["x-abb-signature"]

    def verify(secret: str, body: bytes) -> bool:
        mac = hmac.new(secret.encode(), b"5." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, f"v1={mac}")

    assert verify(SECRET, request.body)
    assert not verify(SECRET, request.body + b" ")
    assert not verify("whsec_other", request.body)


async def test_a_webhook_without_an_origin_has_no_link_rather_than_a_wrong_one() -> None:
    async with TlsReceiver() as receiver:
        a = alert()
        a = Alert(a.alert_id, a.payload, 1, web_origin=None)
        await n.WebhookNotifier(receiver.sender()).send(webhook_destination(receiver.url()), a)
    assert json.loads(receiver.requests[0].body)["link"] is None


@pytest.mark.parametrize(
    "path", ["//evil.example/x", "http://evil.example", "/a b", "/a\r\nb", "/\\x", None, 3]
)
def test_a_link_path_that_is_not_a_plain_absolute_path_is_dropped(path: Any) -> None:
    assert n.absolute_link(ORIGIN, path) is None


async def test_a_webhook_with_no_signing_secret_or_url_is_unreadable_not_sent() -> None:
    async with TlsReceiver() as receiver:
        notifier = n.WebhookNotifier(receiver.sender())
        no_secret = OpenDestination(uuid.uuid4(), "webhook", {"url": receiver.url()}, None)
        no_url = OpenDestination(uuid.uuid4(), "webhook", {}, SECRET)
        assert (await notifier.send(no_secret, alert())).error_code == o.CONFIG_UNREADABLE
        assert (await notifier.send(no_url, alert())).error_code == o.CONFIG_UNREADABLE
    assert receiver.connections == 0


async def test_the_response_body_never_reaches_the_result() -> None:
    async with TlsReceiver(responder=lambda _: (500, {})) as receiver:
        result = await n.WebhookNotifier(receiver.sender()).send(
            webhook_destination(receiver.url()), alert()
        )
    assert result == n.SendResult(False, o.HTTP_5XX, True)
    assert RESPONSE_SENTINEL not in repr(result)


# ---------------------------------------------------------------- Slack

BIDI, PDF, ZW = chr(0x202E), chr(0x202C), chr(0x200B)
FULL_LT, FULL_GT, LINE_SEP = chr(0xFF1C), chr(0xFF1E), chr(0x2028)
HOSTILE_NAMES = [
    "<!channel> wake up",
    "<!here>",
    "<@U12345>",
    "<@U12345|admin>",
    "<#C123|general>",
    "<https://evil.example|click here>",
    "<mailto:a@b.c|x>",
    "&lt;!channel&gt; & more",
    "*bold* _italic_ ~strike~ `code` ```block```",
    "line1\nline2\r\nline3",
    "" + BIDI + "evil" + PDF + " " + ZW + "zero" + ZW + "width",
    "" + FULL_LT + "!channel" + FULL_GT + "",  # full-width < and >
    "x" * 500,
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
async def test_slack_names_cannot_form_links_mentions_or_formatting(name: str) -> None:
    async with TlsReceiver() as receiver:
        sent = alert(
            project={"id": "p", "slug": "a", "name": name},
            agent={"slug": "b", "name": name},
            rule={"id": "r", "name": name},
        )
        destination = OpenDestination(uuid.uuid4(), "slack", {"url": receiver.url()}, None)
        result = await n.SlackNotifier(receiver.sender()).send(destination, sent)
    assert result.ok
    message = json.loads(receiver.requests[0].body)
    text = message["text"]
    assert message["blocks"][0]["text"]["text"] == text
    # The only angle-bracket construct left is our own link.
    stripped = text.replace(
        f"<{ORIGIN}/w/acme/projects/alpha/runs/run_x|Open in Agent BlackBox>", ""
    )
    assert "<" not in stripped and ">" not in stripped
    for token in ("<!", "<@", "<#", "```", BIDI, ZW, "\r"):
        assert token not in stripped
    assert "*" not in stripped.replace("*Run failed*", "")
    assert "`" not in stripped and "~" not in stripped
    assert "<https://evil" not in text
    assert message["unfurl_links"] is False
    assert len(text) < 1500


async def test_a_slack_message_has_the_shape_the_incoming_webhook_expects() -> None:
    async with TlsReceiver() as receiver:
        destination = OpenDestination(uuid.uuid4(), "slack", {"url": receiver.url()}, None)
        await n.SlackNotifier(receiver.sender()).send(destination, alert())
    request = receiver.requests[0]
    message = json.loads(request.body)
    assert set(message) == {"text", "blocks", "unfurl_links", "unfurl_media"}
    assert message["blocks"][0]["type"] == "section"
    assert message["text"].startswith("*Run failed* (high)\nProject: Alpha\nAgent: Coding agent")
    assert "Run status: FAILED" in message["text"]
    assert request.headers["x-abb-alert-id"].startswith("alt_")


def test_slack_escaping_is_idempotent_on_plain_text_and_total_on_control_characters() -> None:
    assert n.slack_escape("plain name 42") == "plain name 42"
    assert n.slack_escape("a<b>&c") == "a&lt;b&gt;&amp;c"


# ---------------------------------------------------------------- email


def mail_settings(sink: SmtpSink, **extra: Any) -> Settings:
    return make_settings(
        "postgresql+asyncpg://x:y@localhost/z",
        smtp_host="127.0.0.1",
        smtp_port=sink.port,
        smtp_from="abb@example.com",
        smtp_starttls=False,
        alerts_smtp_timeout_seconds=5,
        **extra,
    )


def email_destination(*recipients: str) -> OpenDestination:
    return OpenDestination(
        uuid.uuid4(), "email", {"recipients": list(recipients or ["oncall@example.com"])}, None
    )


def parse(raw: bytes) -> email.message.EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)


async def test_email_is_plain_text_with_a_small_html_alternative_to_every_recipient() -> None:
    async with SmtpSink() as sink:
        notifier = n.EmailNotifier(mail_settings(sink))
        sent = alert()
        result = await notifier.send(email_destination("a@example.com", "b@example.org"), sent)
    assert result == n.SendResult(True)
    assert sink.envelopes == [("<abb@example.com>", ["<a@example.com>", "<b@example.org>"])]
    message = parse(sink.messages[0])
    assert message["Subject"] == "[Agent BlackBox] Run failed: Alpha"
    assert message["From"] == "abb@example.com" and "a@example.com" in message["To"]
    assert message["X-ABB-Alert-Id"] == sent.alert_id
    assert sent.alert_id in message["Message-ID"]
    text = message.get_body(("plain",)).get_content()  # type: ignore[union-attr]
    html_part = message.get_body(("html",)).get_content()  # type: ignore[union-attr]
    assert f"Open in Agent BlackBox: {ORIGIN}/w/acme/projects/alpha/runs/run_x" in text
    assert "Project: Alpha" in text and "Agent: Coding agent" in text
    assert f'href="{ORIGIN}/w/acme/projects/alpha/runs/run_x"' in html_part


@pytest.mark.parametrize(
    "name",
    [
        "x\r\nBcc: attacker@evil.example",
        "x\nBcc: attacker@evil.example",
        "x\rSubject: spoof",
        "Alpha" + LINE_SEP + "Bcc: attacker@evil.example",
        "=?utf-8?b?QmNjOiBhdHRhY2tlckBldmlsLmV4YW1wbGU=?=",
    ],
)
async def test_names_cannot_inject_headers_into_an_email(name: str) -> None:
    async with SmtpSink() as sink:
        sent = alert(project={"id": "p", "slug": "a", "name": name})
        result = await n.EmailNotifier(mail_settings(sink)).send(email_destination(), sent)
    assert result.ok
    message = parse(sink.messages[0])
    assert message["Bcc"] is None
    assert len(message.get_all("Subject")) == 1  # type: ignore[arg-type]
    names = {k.lower() for k in message.keys()}
    assert names <= {
        "subject", "from", "to", "date", "message-id", "x-abb-alert-id", "mime-version",
        "content-type",
    }  # fmt: skip
    assert "\n" not in message["Subject"] and "\r" not in message["Subject"]
    assert sink.envelopes[0][1] == ["<oncall@example.com>"]  # nobody was added to the envelope


async def test_names_are_escaped_in_the_html_part() -> None:
    hostile = '<script>alert(1)</script><img src=x onerror=alert(1)> "quoted" & more'
    async with SmtpSink() as sink:
        sent = alert(project={"id": "p", "slug": "a", "name": hostile}, agent=None, rule=None)
        await n.EmailNotifier(mail_settings(sink)).send(email_destination(), sent)
    html_part = parse(sink.messages[0]).get_body(("html",)).get_content()  # type: ignore[union-attr]
    assert "<script>" not in html_part and "<img" not in html_part
    assert "&lt;script&gt;" in html_part and "&amp; more" in html_part


async def test_an_smtp_4xx_is_retryable_and_a_5xx_is_not() -> None:
    async with SmtpSink(fail_with="451 try later") as sink:
        soft = await n.EmailNotifier(mail_settings(sink)).send(email_destination(), alert())
    async with SmtpSink(fail_with="550 no such user") as sink:
        hard = await n.EmailNotifier(mail_settings(sink)).send(email_destination(), alert())
    assert soft == n.SendResult(False, o.SMTP_FAILED, True)
    assert hard == n.SendResult(False, o.SMTP_FAILED, False)


async def test_an_unreachable_relay_is_a_retryable_smtp_failure() -> None:
    async with SmtpSink() as sink:
        settings = mail_settings(sink)
    result = await n.EmailNotifier(settings).send(email_destination(), alert())
    assert result == n.SendResult(False, o.SMTP_FAILED, True)


async def test_starttls_is_used_when_configured_and_verified() -> None:
    cert, key = make_certificate("127.0.0.1")
    async with SmtpSink(starttls_context=server_context(cert, key)) as sink:
        settings = mail_settings(sink).model_copy(update={"smtp_starttls": True})
        trusting = ssl.create_default_context(cadata=cert.decode())
        trusting.check_hostname = False  # the test certificate is for the loopback name
        result = await n.EmailNotifier(settings, tls_context=trusting).send(
            email_destination(), alert()
        )
    assert result.ok and sink.messages and not sink.saw_plaintext_data


async def test_starttls_required_but_not_offered_fails_closed_and_sends_nothing() -> None:
    async with SmtpSink() as sink:  # offers no STARTTLS
        settings = mail_settings(sink).model_copy(update={"smtp_starttls": True})
        result = await n.EmailNotifier(settings).send(email_destination(), alert())
    assert result.error_code == o.SMTP_FAILED
    assert sink.messages == [] and not sink.saw_plaintext_data


async def test_an_untrusted_relay_certificate_fails_closed() -> None:
    cert, key = make_certificate("127.0.0.1")
    async with SmtpSink(starttls_context=server_context(cert, key)) as sink:
        settings = mail_settings(sink).model_copy(update={"smtp_starttls": True})
        result = await n.EmailNotifier(settings).send(email_destination(), alert())  # default trust
    assert result.error_code == o.SMTP_FAILED and sink.messages == []


async def test_email_without_smtp_settings_or_recipients_is_unreadable() -> None:
    plain = make_settings("postgresql+asyncpg://x:y@localhost/z")
    assert (await n.EmailNotifier(plain).send(email_destination(), alert())).error_code == (
        o.CONFIG_UNREADABLE
    )
    async with SmtpSink() as sink:
        bad = OpenDestination(uuid.uuid4(), "email", {"recipients": []}, None)
        assert (await n.EmailNotifier(mail_settings(sink)).send(bad, alert())).error_code == (
            o.CONFIG_UNREADABLE
        )


def test_the_registry_has_the_three_builtin_channels_and_nothing_else() -> None:
    registry = n.build_registry(make_settings("postgresql+asyncpg://x:y@localhost/z"))
    assert set(registry) == {"webhook", "slack", "email"}


def test_clean_text_removes_controls_bidi_and_zero_width_and_bounds_length() -> None:
    assert n.clean_text(f"a{BIDI}b{ZW}c\x00d\n e") == "abcd e"
    assert len(n.clean_text("y" * 1000)) == n.MAX_NAME
    assert n.clean_text(None) == ""
