"""The `Notifier` contract and the three built-in channels (ADR-151).

`Notifier.send(destination, alert) -> SendResult` is an internal contract: webhook, Slack and
email are implemented here and registered by `build_registry`; no third-party code is loaded at
runtime (spec §35, plan D4). A notifier receives a destination whose secrets are already opened
(`OpenDestination`, built by `delivery.py` from the sealed columns and never stored again) and an
`Alert` made of the stored content-free payload. It returns a result with a fixed error code; it
never raises for a delivery problem and never returns or logs a response body.

Names. The project, agent and rule names are the only free text in a payload. They are metadata
every reader of the workspace already sees, but they are typed by people, so each channel treats
them as hostile: control characters, bidirectional overrides and zero-width characters are removed
first; Slack gets `& < >` escaped (so a name cannot form a link, a mention such as `<!channel>` or
`<@U123>`) and its formatting marks removed; email puts them through `html.escape` in the HTML part
and never into a header without the same cleaning (a CRLF cannot start another header, which
`email.message` also refuses).

Links. The payload carries a relative `link_path`; channels make it absolute from `ABB_WEB_ORIGIN`.
Without an origin the alert is sent without a link rather than with a wrong one.
"""

import asyncio
import hashlib
import hmac
import html
import json
import logging
import smtplib
import ssl
import time
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any, Final, Protocol

from abb_api.alerts import model as m
from abb_api.alerts import outbound as o
from abb_api.alerts.payload import TITLES
from abb_api.core.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT: Final = "AgentBlackBox-Alerts/1"
SIGNATURE_VERSION: Final = "v1"
MAX_NAME: Final = 80
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


@dataclass(frozen=True)
class OpenDestination:
    """A destination with its secrets opened. Short-lived: built for one send, never persisted."""

    id: uuid.UUID
    type: str
    config: Mapping[str, Any] = field(repr=False)
    signing_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Alert:
    """What is sent: the stored payload and the context a receiver may dedupe on."""

    alert_id: str  # the public `alr_...` id of the notification
    payload: Mapping[str, Any]
    attempt: int  # 1-based
    web_origin: str | None = None
    test: bool = False


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error_code: str | None = None
    retryable: bool = False

    @staticmethod
    def of(outcome: o.SendOutcome) -> "SendResult":
        return SendResult(outcome.ok, outcome.error_code, outcome.retryable)


class Notifier(Protocol):
    async def send(self, destination: OpenDestination, alert: Alert) -> SendResult: ...


Registry = Mapping[str, Notifier]


# ---------------------------------------------------------------- shared helpers


def clean_text(value: object, limit: int = MAX_NAME) -> str:
    """A name made safe for any channel: printable, single-line, bounded. Not yet escaped."""
    # NFKC first: full-width look-alikes of `<` and `@` become ASCII, which is then escaped.
    text = unicodedata.normalize("NFKC", str(value)) if value is not None else ""
    chars = [
        " " if c.isspace() else c
        for c in text
        if c.isspace() or unicodedata.category(c) not in _DROPPED_CATEGORIES
    ]
    return " ".join("".join(chars).split())[:limit]


def absolute_link(origin: str | None, link_path: object) -> str | None:
    if not origin or not isinstance(link_path, str):
        return None
    if not link_path.startswith("/") or link_path.startswith("//") or "\\" in link_path:
        return None
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in link_path):
        return None
    return origin.rstrip("/") + link_path


def _severity(payload: Mapping[str, Any]) -> str:
    value = payload.get("severity")
    return value if value in m.SEVERITY_RANK else "info"


def _title(payload: Mapping[str, Any]) -> str:
    kind = payload.get("kind")
    return TITLES.get(str(kind), "Alert") if kind != "test" else TEST_TITLE


def _name_of(payload: Mapping[str, Any], key: str) -> str | None:
    node = payload.get(key)
    if not isinstance(node, dict):
        return None
    name = clean_text(node.get("name"))
    return name or None


TEST_TITLE: Final = "Test alert from Agent Black Box"


def _lines(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The labelled facts every channel shows, as (label, cleaned text)."""
    facts = [("Project", _name_of(payload, "project")), ("Agent", _name_of(payload, "agent"))]
    facts.append(("Rule", _name_of(payload, "rule")))
    details = payload.get("details")
    if isinstance(details, dict):
        for key, label in (("run_status", "Run status"), ("finding_severity", "Finding severity")):
            if isinstance(details.get(key), str):
                facts.append((label, clean_text(details[key], 40)))
        if isinstance(details.get("spend_usd"), int | float):
            facts.append(("Spend today (USD)", f"{details['spend_usd']:.2f}"))
        if isinstance(details.get("threshold_usd"), int | float):
            facts.append(("Threshold (USD)", f"{details['threshold_usd']:.2f}"))
    return [(label, text) for label, text in facts if text]


# ---------------------------------------------------------------- webhook


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`v1=` + hex HMAC-SHA256 over `timestamp "." body`, keyed by the destination's secret."""
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


class WebhookNotifier:
    def __init__(
        self,
        http: o.SafeHttpsSender,
        *,
        clock: Callable[[], float] = time.time,
        timeout_seconds: float | None = None,
    ) -> None:
        self._http = http
        self._clock = clock
        self._timeout = timeout_seconds

    async def send(self, destination: OpenDestination, alert: Alert) -> SendResult:
        url = destination.config.get("url")
        if not isinstance(url, str) or not destination.signing_secret:
            return SendResult(False, o.CONFIG_UNREADABLE)
        body_data = dict(alert.payload)
        body_data["link"] = absolute_link(alert.web_origin, alert.payload.get("link_path"))
        body = json.dumps(body_data, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(self._clock()))
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-ABB-Alert-Id": alert.alert_id,  # receivers dedupe on this (at-least-once)
            "X-ABB-Delivery-Attempt": str(alert.attempt),
            "X-ABB-Timestamp": timestamp,
            "X-ABB-Signature": sign(destination.signing_secret, timestamp, body),
        }
        return SendResult.of(
            await self._http.post(url, headers, body, timeout_seconds=self._timeout)
        )


# ---------------------------------------------------------------- Slack


def slack_escape(value: str) -> str:
    """Slack's control characters escaped (`& < >`), formatting marks removed: a name cannot make a
    link, a channel or user mention, a code span or bold text."""
    text = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for mark in "*~`|":
        text = text.replace(mark, "")
    return text


def slack_message(alert: Alert) -> dict[str, Any]:
    payload = alert.payload
    severity = _severity(payload)
    lines = [f"*{slack_escape(_title(payload))}* ({severity})"]
    lines += [f"{label}: {slack_escape(text)}" for label, text in _lines(payload)]
    link = absolute_link(alert.web_origin, payload.get("link_path"))
    if link:
        lines.append(f"<{slack_escape(link)}|Open in Agent Black Box>")
    text = "\n".join(lines)
    return {
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        "unfurl_links": False,
        "unfurl_media": False,
    }


class SlackNotifier:
    def __init__(self, http: o.SafeHttpsSender, *, timeout_seconds: float | None = None) -> None:
        self._http = http
        self._timeout = timeout_seconds

    async def send(self, destination: OpenDestination, alert: Alert) -> SendResult:
        url = destination.config.get("url")
        if not isinstance(url, str):
            return SendResult(False, o.CONFIG_UNREADABLE)
        body = json.dumps(slack_message(alert), separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-ABB-Alert-Id": alert.alert_id,
        }
        return SendResult.of(
            await self._http.post(url, headers, body, timeout_seconds=self._timeout)
        )


# ---------------------------------------------------------------- email


def email_message(alert: Alert, *, sender: str, recipients: list[str]) -> EmailMessage:
    """Plain text plus a small HTML alternative. Names are cleaned (no CR/LF, no controls) before
    they reach a header and `html.escape`d in the HTML part."""
    payload = alert.payload
    title = _title(payload)
    severity = _severity(payload)
    project = _name_of(payload, "project")
    subject = f"[Agent Black Box] {title}" + (f": {project}" if project else "")
    link = absolute_link(alert.web_origin, payload.get("link_path"))
    facts = _lines(payload)

    text_lines = [f"{title} ({severity})", ""]
    text_lines += [f"{label}: {text}" for label, text in facts]
    if link:
        text_lines += ["", f"Open in Agent Black Box: {link}"]
    text_lines += ["", f"Alert id: {alert.alert_id}"]

    rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(text)}</td></tr>"
        for label, text in facts
    )
    anchor = (
        f'<p><a href="{html.escape(link, quote=True)}">Open in Agent Black Box</a></p>'
        if link
        else ""
    )
    page = (
        f"<p><strong>{html.escape(title)}</strong> ({html.escape(severity)})</p>"
        f"<table>{rows}</table>{anchor}<p>Alert id: {html.escape(alert.alert_id)}</p>"
    )

    message = EmailMessage()
    message["Subject"] = subject[:200]
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = f"<{alert.alert_id}@alerts.agent-black-box.invalid>"
    message["X-ABB-Alert-Id"] = alert.alert_id
    message.set_content("\n".join(text_lines))
    message.add_alternative(f"<!doctype html><html><body>{page}</body></html>", subtype="html")
    return message


class EmailNotifier:
    def __init__(self, settings: Settings, *, tls_context: ssl.SSLContext | None = None) -> None:
        self._settings = settings
        self._tls = tls_context  # tests trust a local certificate; production uses the defaults

    async def send(self, destination: OpenDestination, alert: Alert) -> SendResult:
        s = self._settings
        recipients = destination.config.get("recipients")
        if (
            s.smtp_host is None
            or s.smtp_from is None
            or not isinstance(recipients, list)
            or not recipients
            or not all(isinstance(r, str) for r in recipients)
        ):
            return SendResult(False, o.CONFIG_UNREADABLE)
        try:
            message = email_message(alert, sender=s.smtp_from, recipients=list(recipients))
        except ValueError:
            return SendResult(False, o.CONFIG_UNREADABLE)  # a header the library refuses
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._deliver, message, list(recipients)),
                timeout=s.alerts_smtp_timeout_seconds * 2,
            )
        except TimeoutError:
            return SendResult(False, o.TIMEOUT, retryable=True)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            return SendResult(False, o.SMTP_FAILED, retryable=all(400 <= c < 500 for c in codes))
        except smtplib.SMTPResponseException as exc:
            # 4xx: the relay asked us to try again later. 5xx and authentication failures will
            # not change by themselves.
            return SendResult(False, o.SMTP_FAILED, retryable=400 <= exc.smtp_code < 500)
        except (smtplib.SMTPException, OSError, ssl.SSLError):
            return SendResult(False, o.SMTP_FAILED, retryable=True)
        return SendResult(True)

    def _deliver(self, message: EmailMessage, recipients: list[str]) -> None:
        s = self._settings
        assert s.smtp_host is not None
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.alerts_smtp_timeout_seconds) as smtp:
            smtp.ehlo()
            if s.smtp_starttls:
                # Fails closed: a relay without STARTTLS raises instead of receiving plaintext.
                smtp.starttls(context=self._tls or ssl.create_default_context())
                smtp.ehlo()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(message, from_addr=s.smtp_from, to_addrs=recipients)


# ---------------------------------------------------------------- registry


def _default_sender(settings: Settings) -> o.SafeHttpsSender:
    if settings.alerts_test_network_file is not None:  # development/test only; see testnet.py
        from abb_api.alerts.testnet import sender_from_file

        return sender_from_file(
            settings.alerts_test_network_file,
            timeout_seconds=settings.alerts_request_timeout_seconds,
        )
    return o.SafeHttpsSender(timeout_seconds=settings.alerts_request_timeout_seconds)


def build_registry(
    settings: Settings,
    http: o.SafeHttpsSender | None = None,
    *,
    smtp_tls_context: ssl.SSLContext | None = None,
) -> Registry:
    sender = http or _default_sender(settings)
    return {
        m.WEBHOOK: WebhookNotifier(sender),
        m.SLACK: SlackNotifier(sender),
        m.EMAIL: EmailNotifier(settings, tls_context=smtp_tls_context),
    }
