"""The outbound HTTPS sender for webhook and Slack destinations (ADR-151).

This is the SSRF defence; the target guard at save time (`targets.py`) is only an early filter.
What it does, in order, for every request:

1. Only `https://` URLs with a hostname or a public IP literal are sent.
2. The hostname is resolved **now**, at delivery time, through an injectable resolver. If any
   returned address is not public (`targets.is_public_ip`: private, loopback, link-local including
   169.254.169.254, CGNAT, multicast, IPv6 unique-local, IPv4-mapped/6to4/Teredo/NAT64 judged by the
   IPv4 they carry) the request is refused (`blocked_address`); one bad answer poisons the set, so
   a mixed answer cannot be used to slip through.
3. The connection goes to the address that was checked (the IP is pinned), with the original
   hostname as TLS SNI and the certificate verified against that hostname. The name is never
   resolved a second time, so DNS rebinding between the check and the connection cannot swap the
   address.
4. No redirect is ever followed (a 3xx is a failure, `http_3xx`).
5. The port must be in the allow-list (`ABB_ALERTS_ALLOWED_PORTS`, 443 by default) even though the
   target was checked at save time, because the list can be tightened later (`blocked_port`,
   permanent, nothing is resolved or connected).
6. One total deadline covers resolution, connect, TLS, write and the read of the response head.
7. The response is read only as far as the status line (capped); the body is never read, stored
   or logged, and the connection is closed.

The HTTP/1.1 request is written by hand on an asyncio TLS stream because no general client lets a
caller pin the connection address while keeping SNI, `Host` and certificate verification on the
hostname. It sends one `POST` with `Connection: close` and reads one status line, which is all
delivery needs. Nothing here logs a URL, a header or a response.
"""

import asyncio
import contextlib
import ipaddress
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote, urlsplit

from abb_api.alerts.targets import DEFAULT_ALLOWED_PORTS, DEFAULT_PORT, is_public_ip

# Fixed error vocabulary (stored in `alert_notifications.last_error_code`).
TIMEOUT: Final = "timeout"
CONNECT_FAILED: Final = "connect_failed"
DNS_FAILED: Final = "dns_failed"
TLS_FAILED: Final = "tls_failed"
BLOCKED_ADDRESS: Final = "blocked_address"
BLOCKED_PORT: Final = "blocked_port"  # a destination saved before the allow-list was tightened
HTTP_3XX: Final = "http_3xx"
HTTP_4XX: Final = "http_4xx"
HTTP_5XX: Final = "http_5xx"
BAD_RESPONSE: Final = "bad_response"
SMTP_FAILED: Final = "smtp_failed"
CONFIG_UNREADABLE: Final = "config_unreadable"
INTERNAL_ERROR: Final = "internal_error"
ERROR_CODES: Final = frozenset({
    TIMEOUT, CONNECT_FAILED, DNS_FAILED, TLS_FAILED, BLOCKED_ADDRESS, BLOCKED_PORT, HTTP_3XX,
    HTTP_4XX, HTTP_5XX, BAD_RESPONSE, SMTP_FAILED, CONFIG_UNREADABLE, INTERNAL_ERROR,
})  # fmt: skip
# Worth another attempt: the receiver may come back. The others need a person to change something.
RETRYABLE_CODES: Final = frozenset({TIMEOUT, CONNECT_FAILED, DNS_FAILED, HTTP_5XX, INTERNAL_ERROR})

MAX_HEAD_BYTES: Final = 16 * 1024
MAX_ADDRESSES: Final = 4
_STATUS_LINE = re.compile(rb"^HTTP/1\.[01] ([1-5][0-9]{2})(?:[ \r\n]|$)")
_BAD_HEADER = re.compile(r"[\x00-\x1f\x7f]")
_RETRYABLE_STATUS = frozenset({408, 425, 429})
_SAFE_PATH = "/%:@!$&'()*+,;=-._~"

Resolver = Callable[[str, int], Awaitable[list[str]]]
Opener = Callable[
    [str, int, ssl.SSLContext, str],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    error_code: str | None = None
    retryable: bool = False
    status: int | None = None  # the HTTP status; never a body

    @staticmethod
    def success(status: int | None = None) -> "SendOutcome":
        return SendOutcome(True, None, False, status)

    @staticmethod
    def failure(
        code: str, *, retryable: bool | None = None, status: int | None = None
    ) -> "SendOutcome":
        return SendOutcome(
            False, code, code in RETRYABLE_CODES if retryable is None else retryable, status
        )


async def system_resolver(host: str, port: int) -> list[str]:
    """Every address the system resolver returns for the name (A and AAAA), in order."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


async def system_opener(
    ip: str, port: int, context: ssl.SSLContext, server_hostname: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to `ip` (not a name) and run TLS with `server_hostname` as SNI and as the name the
    certificate must match."""
    return await asyncio.open_connection(ip, port, ssl=context, server_hostname=server_hostname)


class SafeHttpsSender:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        opener: Opener = system_opener,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 5.0,
        allowed_ports: Collection[int] = DEFAULT_ALLOWED_PORTS,
    ) -> None:
        self._allowed_ports = frozenset(allowed_ports)
        self._resolver = resolver
        self._opener = opener
        self._context = ssl_context or ssl.create_default_context()
        self._timeout = timeout_seconds

    async def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        timeout_seconds: float | None = None,
    ) -> SendOutcome:
        """POST `body` to `url`. Never raises: every failure is a fixed code in the outcome."""
        try:
            async with asyncio.timeout(timeout_seconds or self._timeout):
                return await self._post(url, headers, body)
        except TimeoutError:
            return SendOutcome.failure(TIMEOUT)
        except Exception:
            return SendOutcome.failure(INTERNAL_ERROR)

    async def _post(self, url: str, headers: Mapping[str, str], body: bytes) -> SendOutcome:
        try:
            parts = urlsplit(url)
            port = DEFAULT_PORT if parts.port is None else parts.port
            host = parts.hostname
        except ValueError:
            return SendOutcome.failure(CONFIG_UNREADABLE)
        if parts.scheme != "https" or not host or parts.username or parts.password:
            return SendOutcome.failure(CONFIG_UNREADABLE)  # saved targets are https; refuse anyway
        if port not in self._allowed_ports:
            return SendOutcome.failure(BLOCKED_PORT)  # before any lookup or connection
        try:
            request = _request(parts.path, parts.query, host, port, headers, body)
        except ValueError:
            return SendOutcome.failure(CONFIG_UNREADABLE)

        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except OSError:
                return SendOutcome.failure(DNS_FAILED)
        if not addresses:
            return SendOutcome.failure(DNS_FAILED)
        checked: list[str] = []
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError:
                return SendOutcome.failure(BLOCKED_ADDRESS)
            if not is_public_ip(ip):
                return SendOutcome.failure(BLOCKED_ADDRESS)
            checked.append(str(ip))

        last = SendOutcome.failure(CONNECT_FAILED)
        for ip_text in checked[:MAX_ADDRESSES]:
            try:
                reader, writer = await self._opener(ip_text, port, self._context, host)
            except ssl.SSLError:
                return SendOutcome.failure(TLS_FAILED)
            except TimeoutError:
                raise
            except OSError:
                last = SendOutcome.failure(CONNECT_FAILED)
                continue
            return await _exchange(reader, writer, request)
        return last


def _request(
    path: str, query: str, host: str, port: int, headers: Mapping[str, str], body: bytes
) -> bytes:
    target = quote(path or "/", safe=_SAFE_PATH)
    if query:
        target += "?" + quote(query, safe=_SAFE_PATH + "?")
    shown = f"[{host}]" if ":" in host else host
    lines = [
        f"POST {target} HTTP/1.1",
        f"Host: {shown}" + ("" if port == 443 else f":{port}"),
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    for name, value in headers.items():
        if not name or ":" in name or _BAD_HEADER.search(name) or _BAD_HEADER.search(value):
            raise ValueError("bad header")  # never let a value start a second header or request
        lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


async def _exchange(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, request: bytes
) -> SendOutcome:
    try:
        writer.write(request)
        await writer.drain()
        head = b""
        while b"\n" not in head and len(head) < MAX_HEAD_BYTES:
            chunk = await reader.read(2048)
            if not chunk:
                break
            head += chunk
        match = _STATUS_LINE.match(head)
        if match is None or b"\n" not in head:  # no complete status line within the cap
            return SendOutcome.failure(BAD_RESPONSE)
        status = int(match.group(1))
        if 200 <= status < 300:
            return SendOutcome.success(status)
        if 300 <= status < 400:
            return SendOutcome.failure(HTTP_3XX, status=status)  # never followed
        if status >= 500:
            return SendOutcome.failure(HTTP_5XX, status=status)
        if status < 200:
            return SendOutcome.failure(BAD_RESPONSE, status=status)
        return SendOutcome.failure(HTTP_4XX, retryable=status in _RETRYABLE_STATUS, status=status)
    except ssl.SSLError:
        return SendOutcome.failure(TLS_FAILED)
    except OSError:
        return SendOutcome.failure(CONNECT_FAILED)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            writer.transport.abort()  # do not wait for a TLS close_notify from a slow peer
