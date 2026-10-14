"""Validation and masking of destination targets (ADR-150).

Early guard, not the defence. Saving a destination never resolves a name: a hostname that points at
a private address passes here and is refused at delivery time, when the sender resolves it and
connects to the resolved address (step 2). What this module refuses is what is wrong on its face:
plain HTTP, credentials in the URL, IP literals in non-public ranges (including the cloud metadata
address), forms of IP literals that some resolvers accept (`127.1`, `0x7f000001`, `2130706433`),
single-label and obviously internal names. Every refusal is a fixed reason code; the value is never
echoed in a message, a log line or an audit row.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

MAX_URL_LENGTH: Final = 2048
MAX_RECIPIENTS: Final = 10
MAX_EMAIL_LENGTH: Final = 254
MAX_HOST_LENGTH: Final = 253

# Names that are internal by convention. A hostname outside this list can still resolve to a
# private address; the sender's resolution check is what stops that.
_INTERNAL_SUFFIXES: Final = (
    ".localhost", ".local", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet",
    ".private", ".home.arpa",
)  # fmt: skip
_NAT64 = ipaddress.ip_network("64:ff9b::/96")  # RFC 6052: carries an IPv4 address in its low bits
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_ALL_DIGITS = re.compile(r"^[0-9]+$")
_HEX_FORM = re.compile(r"^0x[0-9a-f]*$")
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_LOCAL_PART = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$")


class TargetInvalid(Exception):
    """The target is not acceptable. `reason` is a fixed code from `REASONS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


REASONS: Final = frozenset({
    "url_required", "url_too_long", "url_malformed", "not_https", "credentials_in_url",
    "host_invalid", "host_internal", "ip_not_public", "ip_literal_form", "port_invalid",
    "fragment_not_allowed", "recipients_required", "too_many_recipients", "recipient_invalid",
    "target_shape",
})  # fmt: skip


@dataclass(frozen=True)
class WebTarget:
    url: str  # normalised: scheme and host lower-case, no fragment
    host: str
    masked: str


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # An IPv4 address wrapped in IPv6 is judged as the IPv4 address it carries.
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            return is_public_ip(embedded)
        if ip in _NAT64:
            return is_public_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.teredo is not None:
            return all(is_public_ip(part) for part in ip.teredo)
    return bool(ip.is_global) and not ip.is_multicast


def _host_of(raw_netloc_host: str) -> str:
    host = raw_netloc_host.lower()
    if host.endswith("."):
        raise TargetInvalid("host_invalid")  # a trailing dot is a second spelling of the same name
    return host


def validate_web_url(value: object) -> WebTarget:
    """A webhook or Slack URL, or `TargetInvalid`."""
    if not isinstance(value, str) or not value.strip():
        raise TargetInvalid("url_required")
    if len(value) > MAX_URL_LENGTH:
        raise TargetInvalid("url_too_long")
    if _CONTROL.search(value):
        raise TargetInvalid("url_malformed")
    try:
        parts = urlsplit(value)
        port = parts.port
        raw_host = parts.hostname
    except ValueError:
        raise TargetInvalid("url_malformed") from None
    if parts.scheme.lower() != "https":
        raise TargetInvalid("not_https")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise TargetInvalid("credentials_in_url")
    if not raw_host:
        raise TargetInvalid("host_invalid")
    if parts.fragment:
        raise TargetInvalid("fragment_not_allowed")
    if port is not None and not 1 <= port <= 65535:
        raise TargetInvalid("port_invalid")
    host = _host_of(raw_host)
    if len(host) > MAX_HOST_LENGTH:
        raise TargetInvalid("host_invalid")

    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise TargetInvalid("ip_not_public")
    else:
        labels = host.split(".")
        # `127.1`, `0x7f.1` and `2130706433` are IPv4 addresses to many resolvers but hostnames to
        # `ipaddress`: a name whose last label is numeric is never a real DNS name.
        if _ALL_DIGITS.match(labels[-1]) or _HEX_FORM.match(labels[-1]):
            raise TargetInvalid("ip_literal_form")
        if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
            # one label ("intranet", "localhost") cannot be a public name; xn-- labels are ASCII
            raise TargetInvalid("host_internal" if len(labels) < 2 else "host_invalid")
        if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
            raise TargetInvalid("host_internal")

    shown_host = f"[{host}]" if literal is not None and literal.version == 6 else host
    netloc = shown_host if port in (None, 443) else f"{shown_host}:{port}"
    path_and_query = parts.path + (f"?{parts.query}" if parts.query else "")
    normalised = f"https://{netloc}{path_and_query}"
    return WebTarget(url=normalised, host=host, masked=mask_url(netloc, parts.path))


def mask_url(netloc: str, path: str) -> str:
    """`https://host/…/abcd`: the host, an ellipsis and, when the last path segment looks like a
    token (12+ characters), its last four characters so two hooks on one host can be told apart.
    A short last segment, the query and everything else are never shown."""
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if len(last) >= 12:
        return f"https://{netloc}/…/{last[-4:]}"
    return f"https://{netloc}/…" if path.strip("/") else f"https://{netloc}"


def validate_recipients(value: object) -> list[str]:
    """Normalised (lower-case domain, de-duplicated, sorted) recipients, or `TargetInvalid`."""
    if not isinstance(value, list) or not value:
        raise TargetInvalid("recipients_required")
    if len(value) > MAX_RECIPIENTS:
        raise TargetInvalid("too_many_recipients")
    seen: dict[str, str] = {}
    for item in value:
        address = _valid_address(item)
        seen.setdefault(address.lower(), address)
    return sorted(seen.values(), key=str.lower)


def _valid_address(item: object) -> str:
    if not isinstance(item, str) or len(item) > MAX_EMAIL_LENGTH or item.count("@") != 1:
        raise TargetInvalid("recipient_invalid")
    local, domain = item.split("@")
    domain = domain.lower()
    labels = domain.split(".")
    if (
        not _LOCAL_PART.match(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or _ALL_DIGITS.match(labels[-1])
    ):
        raise TargetInvalid("recipient_invalid")
    return f"{local}@{domain}"


def mask_recipients(recipients: list[str]) -> str:
    """`a***@example.com, b***@example.org (+2 more)`: the first character of each local part."""
    shown = [f"{r.split('@')[0][:1]}***@{r.split('@')[1]}" for r in recipients[:3]]
    text = ", ".join(shown)
    return text if len(recipients) <= 3 else f"{text} (+{len(recipients) - 3} more)"
