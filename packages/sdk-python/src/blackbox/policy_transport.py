"""One bounded HTTP exchange with the policy service (standard library only).

`guard()` runs on the caller's thread, so a request must end by its deadline however the server
behaves. `http.client` only bounds each socket operation; here the socket is wrapped so every read
and write also checks one deadline for the whole exchange, and the response body is read through a
size cap. One connection per request (`Connection: close`): the policy calls are rare next to
events, and there is no shared connection state to corrupt between threads.

Not interruptible: name resolution (`getaddrinfo`) and the TLS handshake's first bytes. Both are
bounded by the operating system's own limits, not by the deadline; the documentation says so.
"""

import http.client
import io
import json
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

MAX_BODY_BYTES = 64 * 1024  # every policy response is a few hundred bytes
_MAX_HEADER_BYTES = 16 * 1024

REASON_UNREACHABLE = "policy_service_unreachable"
REASON_TIMEOUT = "policy_timeout"
REASON_ERROR = "policy_error"

Clock = Callable[[], float]


class TransportFailure(Exception):
    """The exchange did not produce a usable HTTP reply. `reason` is a fixed vocabulary word."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, Any] | None  # a JSON object, else None
    retry_after: float | None
    server_time: float | None  # the server's `Date`, as a Unix time (skew-free expiry maths)

    @property
    def code(self) -> str | None:
        """The API's error `code`, when the body is the standard error envelope."""
        body = self.body
        if body is None:
            return None
        error = body.get("error")
        found = error.get("code") if isinstance(error, dict) else body.get("code")
        return found if isinstance(found, str) and len(found) <= 64 else None


class _Raw(io.RawIOBase):
    def __init__(self, sock: socket.socket, deadline: float, clock: Clock) -> None:
        self._sock, self._deadline, self._clock = sock, deadline, clock
        self._seen = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        left = self._deadline - self._clock()
        if left <= 0:
            raise TimeoutError("deadline")
        self._sock.settimeout(left)
        size = self._sock.recv_into(buffer)
        self._seen += size
        if self._seen > MAX_BODY_BYTES + _MAX_HEADER_BYTES:
            raise OSError("response too large")
        return size


class _DeadlineSocket:
    """The slice of a socket `http.client` uses, with one deadline over all of it."""

    def __init__(self, sock: socket.socket, deadline: float, clock: Clock) -> None:
        self._sock, self._deadline, self._clock = sock, deadline, clock

    def sendall(self, data: Any) -> None:
        left = self._deadline - self._clock()
        if left <= 0:
            raise TimeoutError("deadline")
        self._sock.settimeout(left)
        self._sock.sendall(data)

    def makefile(self, *_args: Any, **_kwargs: Any) -> io.BufferedReader:
        return io.BufferedReader(_Raw(self._sock, self._deadline, self._clock))

    def close(self) -> None:
        # `http.client` calls this as soon as it has the headers of a `Connection: close` reply
        # and expects the file object from `makefile()` to keep the socket alive until the body is
        # read (a real socket counts its file objects). Ours does not, so closing here read the
        # body from a closed socket (EBADF) whenever it was not already buffered: a spent approval
        # reported as an outage. `PolicyTransport.request` closes the real socket, once, in
        # `finally`.
        return None

    def settimeout(self, _value: float | None) -> None:
        return None

    def shutdown(self, how: int) -> None:
        self._sock.shutdown(how)


def parse_retry_after(value: str | None, cap: float) -> float | None:
    """Seconds from a Retry-After header (delta-seconds), capped; None if unusable."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if seconds != seconds or seconds < 0:  # NaN, negative
        return None
    return min(seconds, cap)


def _server_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


class PolicyTransport:
    """Builds requests against one endpoint with one API key. Holds no connection."""

    def __init__(self, endpoint: str, api_key: str, clock: Clock = time.monotonic) -> None:
        parts = urlsplit(endpoint)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or "localhost"
        self._port = parts.port or (443 if self._https else 80)
        self._prefix = parts.path.rstrip("/")
        self._key = api_key
        self._clock = clock

    @staticmethod
    def path(*segments: str) -> str:
        return "/" + "/".join(quote(s, safe="") for s in segments)

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        timeout: float,
        retry_after_cap: float = 60.0,
    ) -> Reply:
        """One exchange that ends within `timeout` seconds (plus name resolution), or raises
        TransportFailure with a fixed reason. Never returns a body over `MAX_BODY_BYTES`."""
        deadline = self._clock() + timeout
        sock: socket.socket | None = None
        conn: http.client.HTTPConnection | None = None
        try:
            sock = socket.create_connection((self._host, self._port), timeout=timeout)
            if self._https:
                left = max(0.001, deadline - self._clock())
                sock.settimeout(left)
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self._host)
            conn = http.client.HTTPConnection(self._host, self._port)
            conn.sock = _DeadlineSocket(sock, deadline, self._clock)
            payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
            headers = {
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
                "Connection": "close",
                "User-Agent": "agent-black-box-python",
            }
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, self._prefix + path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read(MAX_BODY_BYTES + 1)
            if len(data) > MAX_BODY_BYTES:
                raise TransportFailure(REASON_ERROR)
            return Reply(
                response.status,
                _json_object(data),
                parse_retry_after(response.getheader("Retry-After"), retry_after_cap),
                _server_time(response.getheader("Date")),
            )
        except TransportFailure:
            raise
        except TimeoutError:
            raise TransportFailure(REASON_TIMEOUT) from None
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise TransportFailure(REASON_UNREACHABLE) from None
        except ValueError:  # an unparsable status line or header
            raise TransportFailure(REASON_ERROR) from None
        finally:
            for closable in (conn, sock):
                if closable is not None:
                    try:
                        closable.close()
                    except Exception:  # noqa: S110  (closing a dead socket must not raise)
                        pass


def _json_object(data: bytes) -> dict[str, Any] | None:
    if not data:
        return None
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None
