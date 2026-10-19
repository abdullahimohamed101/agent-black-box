#!/usr/bin/env python3
"""Assertions for a hosted Agent BlackBox behind a reverse proxy (Phase 23, ADR-178).

Run through `scripts/hosting-verify.sh`; the same assertions run against the local rig
(`scripts/hosting-e2e.sh`, Caddy with its local CA) and against a real URL. Standard library only, so it
runs on the server without installing anything. Each check prints `PASS`, `FAIL` or `SKIP`; the exit
status is non-zero on any FAIL. It never prints a token, a cookie value or a secret: only names, status
codes and counts.

Every wait is a bounded poll with a generous bound. A bound is a deadline that separates "arrived
incrementally" from "was buffered until the keep-alive", not a latency measurement.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import select
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit

MAX_BODY_READ = 2 * 1024 * 1024
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
DEV_DEFAULTS = (
    "abb_dev_password",
    "abb_runtime_dev_password",
    "abb_retention_dev_password",
    "abb_grafana_dev_password",
)
# Compose services that hold tenant data or run the product: never published to the host.
UNPUBLISHED = ("postgres", "migrate", "api", "worker", "retention", "web", "backup")


@dataclass
class Result:
    check: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""

    def line(self) -> str:
        return f"{self.status:<5}{self.check}" + (f"  {self.detail}" if self.detail else "")


def ok(check: str, detail: str = "") -> Result:
    return Result(check, "PASS", detail)


def bad(check: str, detail: str) -> Result:
    return Result(check, "FAIL", detail)


def skip(check: str, detail: str) -> Result:
    return Result(check, "SKIP", detail)


def verdict(check: str, good: bool, ok_detail: str, bad_detail: str) -> Result:
    return ok(check, ok_detail) if good else bad(check, bad_detail)


# --------------------------------------------------------------------------- HTTP


@dataclass
class Response:
    status: int
    headers: List[Tuple[str, str]]
    body: bytes = b""

    def header(self, name: str) -> Optional[str]:
        values = self.all(name)
        return values[0] if values else None

    def all(self, name: str) -> List[str]:
        low = name.lower()
        return [v for k, v in self.headers if k.lower() == low]

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class Target:
    """One base URL, an optional CA file and an optional address override (the rig resolves names itself)."""

    def __init__(
        self,
        base_url: str,
        cacert: Optional[str] = None,
        resolve: Optional[str] = None,
        http_port: Optional[int] = None,
        timeout: float = 30.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("base URL must be http(s)://host[:port]")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.http_port = http_port if http_port is not None else 80
        self.cacert = cacert
        self.resolve = resolve
        self.timeout = timeout

    def host_header(self, scheme: Optional[str] = None, port: Optional[int] = None) -> str:
        """What a browser sends: the port only when it is not the scheme's default."""
        scheme = scheme or self.scheme
        port = port or self.port
        return self.host if port == (443 if scheme == "https" else 80) else f"{self.host}:{port}"

    def _context(self) -> ssl.SSLContext:
        # Verification stays on: a certificate that does not verify is itself a failed check.
        return ssl.create_default_context(cafile=self.cacert)

    def connect(self, scheme: Optional[str] = None, port: Optional[int] = None) -> socket.socket:
        scheme = scheme or self.scheme
        port = port or self.port
        raw = socket.create_connection((self.resolve or self.host, port), timeout=self.timeout)
        if scheme == "http":
            return raw
        try:
            return self._context().wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        scheme: Optional[str] = None,
        port: Optional[int] = None,
        attempts: int = 3,
    ) -> Response:
        """One request over a fresh connection. Idempotent requests retry a refused or reset connection."""
        last: Optional[BaseException] = None
        for attempt in range(attempts if method in ("GET", "HEAD") else 1):
            try:
                return self._once(method, path, headers or {}, body, scheme, port)
            except ssl.SSLCertVerificationError:
                raise
            except (ConnectionError, socket.timeout, ssl.SSLError, OSError) as exc:
                last = exc
                time.sleep(1.0 + attempt)
        if last is None:
            return self._once(method, path, headers or {}, body, scheme, port)
        raise last

    def _once(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: Optional[bytes],
        scheme: Optional[str],
        port: Optional[int],
    ) -> Response:
        sock = self.connect(scheme, port)
        try:
            return _exchange(sock, self.host_header(scheme, port), method, path, headers, body)
        finally:
            sock.close()


def _build_head(host: str, method: str, path: str, headers: Dict[str, str], length: Optional[int]) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    names = {k.lower() for k in headers}
    if "accept-encoding" not in names:
        lines.append("Accept-Encoding: identity")
    if length is not None:
        lines.append(f"Content-Length: {length}")
    lines.extend(f"{k}: {v}" for k, v in headers.items())
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _read_head(sock: socket.socket, deadline: float) -> Tuple[int, List[Tuple[str, str]], bytes]:
    """Read until the end of the response head. Returns (status, headers, bytes already read after the head)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        if time.monotonic() > deadline:
            raise socket.timeout("no response head before the deadline")
        chunk = sock.recv(65536)
        if not chunk:
            if not buf:
                raise ConnectionResetError("connection closed before a response")
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = []
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.append((k.strip(), v.strip()))
    return status, headers, rest


def _dechunk(data: bytes) -> bytes:
    out = b""
    while data:
        size_line, _, data = data.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out += data[:size]
        data = data[size + 2 :]
    return out


def _exchange(
    sock: socket.socket,
    host: str,
    method: str,
    path: str,
    headers: Dict[str, str],
    body: Optional[bytes],
) -> Response:
    sock.settimeout(30.0)
    length = len(body) if body is not None else None
    sock.sendall(_build_head(host, method, path, headers, length))
    if body is not None:
        # The proxy may answer (413) and close before the whole body is sent; read the answer instead of failing.
        view = memoryview(body)
        sent = 0
        try:
            while sent < len(view):
                readable, _, _ = select.select([sock], [], [], 0)
                if readable:
                    break
                sent += sock.send(view[sent : sent + 65536])
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass
    deadline = time.monotonic() + 60.0
    status, hdrs, rest = _read_head(sock, deadline)
    chunks = [rest]
    total = len(rest)
    while total < MAX_BODY_READ and time.monotonic() < deadline:
        try:
            data = sock.recv(65536)
        except (socket.timeout, ConnectionError, ssl.SSLError):
            break
        if not data:
            break
        chunks.append(data)
        total += len(data)
    raw = b"".join(chunks)
    names = {k.lower(): v for k, v in hdrs}
    if names.get("transfer-encoding", "").lower() == "chunked":
        raw = _dechunk(raw)
    return Response(status, hdrs, raw)


@dataclass
class StreamProbe:
    status: int = 0
    headers: List[Tuple[str, str]] = field(default_factory=list)
    arrivals: List[Tuple[float, bytes]] = field(default_factory=list)  # (seconds since the request, bytes)
    error: str = ""

    def header(self, name: str) -> Optional[str]:
        low = name.lower()
        for k, v in self.headers:
            if k.lower() == low:
                return v
        return None


def probe_stream(
    target: Target, path: str, headers: Dict[str, str], first_bound: float, heartbeat_bound: float
) -> StreamProbe:
    """Open an event stream and record when bytes arrive, over a raw socket so no layer buffers for us.

    Stops once the first data and a later heartbeat were seen, or when `first_bound + heartbeat_bound`
    has passed. The chunk framing is left in place: the checks look for text, not for frames.
    """
    probe = StreamProbe()
    started = time.monotonic()
    try:
        sock = target.connect()
    except Exception as exc:  # noqa: BLE001 - reported as a failed check, not a crash
        probe.error = f"connect: {type(exc).__name__}"
        return probe
    try:
        all_headers = dict(headers)
        all_headers.setdefault("Accept", "text/event-stream")
        all_headers.setdefault("Accept-Encoding", "gzip, zstd, br")
        sock.sendall(_build_head(target.host_header(), "GET", path, all_headers, None))
        sock.settimeout(0.5)
        buf = b""
        end = started + first_bound + heartbeat_bound
        head_done = False
        while time.monotonic() < end:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except (ConnectionError, ssl.SSLError):
                break
            if not data:
                break
            now = time.monotonic() - started
            if not head_done:
                buf += data
                if b"\r\n\r\n" not in buf:
                    continue
                head, _, rest = buf.partition(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                probe.status = int(lines[0].split(" ", 2)[1])
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        probe.headers.append((k.strip(), v.strip()))
                head_done = True
                data = rest
                if probe.status != 200:
                    break
                if not data:
                    continue
            probe.arrivals.append((now, data))
            if len(probe.arrivals) >= 2 and probe.arrivals[-1][0] - probe.arrivals[0][0] > 0.2:
                break
    except Exception as exc:  # noqa: BLE001
        probe.error = type(exc).__name__
    finally:
        sock.close()
    return probe


# --------------------------------------------------------------------------- options


@dataclass
class Options:
    metrics_token: Optional[str] = None
    stream_path: Optional[str] = None
    stream_token: Optional[str] = None
    web_budget: int = 10  # sign-in starts per address per minute (apps/web loginLimiter)
    api_budget: int = 30  # failed API-key attempts per address per window (ABB_AUTH_FAIL_PER_ADDRESS)
    session_global: int = 600  # failed session attempts per window across all addresses (ABB_SESSION_FAIL_GLOBAL)
    first_bound: float = 10.0
    heartbeat_bound: float = 40.0
    over_limit_bytes: int = 9 * 1024 * 1024
    under_limit_bytes: int = 7 * 1024 * 1024
    flood: bool = True
    signin_email: Optional[str] = None  # a person invited to a workspace (the rig's fake identity provider accepts any email)
    signin_stranger: Optional[str] = None  # a person nobody invited


def _random_key() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    key_id = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"abb_live_{key_id}.{secrets.token_urlsafe(32)}"


def _random_ip() -> str:
    return ".".join(str(secrets.randbelow(200) + 20) for _ in range(4))


# --------------------------------------------------------------------------- checks


def check_tls(t: Target) -> List[Result]:
    out: List[Result] = []
    if t.scheme != "https":
        return [bad("tls.certificate", "the base URL is not https")]
    try:
        sock = t.connect()
    except ssl.SSLCertVerificationError as exc:
        return [bad("tls.certificate", f"certificate does not verify: {exc.verify_message}")]
    except Exception as exc:  # noqa: BLE001
        return [bad("tls.certificate", f"could not connect: {type(exc).__name__}")]
    try:
        cert = sock.getpeercert() or {}
        version = sock.version()
    finally:
        sock.close()
    expiry = ssl.cert_time_to_seconds(cert["notAfter"]) if cert.get("notAfter") else 0
    days = (expiry - time.time()) / 86400
    out.append(
        verdict(
            "tls.certificate",
            days > 0 and version in ("TLSv1.2", "TLSv1.3"),
            f"verified for {t.host}, {version}, {days:.0f} days left",
            f"expired or weak protocol ({version}, {days:.1f} days)",
        )
    )
    try:
        r = t.request("GET", "/healthz", scheme="http", port=t.http_port, attempts=2)
    except Exception as exc:  # noqa: BLE001
        out.append(bad("tls.http_redirect", f"plain http not answered: {type(exc).__name__}"))
        return out
    loc = r.header("location") or ""
    good = r.status in (301, 302, 307, 308) and loc.startswith("https://" + t.host)
    out.append(
        verdict(
            "tls.http_redirect",
            good,
            f"http {r.status} to https",
            f"plain http answered {r.status} without redirecting to https://{t.host}",
        )
    )
    return out


def _nonce(csp: str) -> Optional[str]:
    m = re.search(r"script-src[^;]*'nonce-([A-Za-z0-9+/=_-]+)'", csp)
    return m.group(1) if m else None


def check_headers(t: Target) -> List[Result]:
    out: List[Result] = []
    page = t.request("GET", "/", {"Accept": "text/html"})
    page2 = t.request("GET", "/", {"Accept": "text/html"})
    api = t.request("GET", "/v1/me")
    for label, r in (("web", page), ("api", api)):
        values = r.all("Strict-Transport-Security")
        good = len(values) == 1 and "max-age=" in values[0] and int(re.search(r"max-age=(\d+)", values[0]).group(1)) >= 31536000  # type: ignore[union-attr]
        out.append(
            verdict(
                f"headers.hsts_once.{label}",
                good,
                "Strict-Transport-Security present exactly once",
                f"Strict-Transport-Security appears {len(values)} time(s) (want exactly 1, max-age >= 1 year)",
            )
        )
    csp1, csp2 = page.header("Content-Security-Policy") or "", page2.header("Content-Security-Policy") or ""
    n1, n2 = _nonce(csp1), _nonce(csp2)
    script = re.search(r"script-src([^;]*)", csp1)
    out.append(
        verdict(
            "headers.csp_nonce_changes",
            bool(n1 and n2 and n1 != n2),
            "a fresh script nonce on every request",
            "the script nonce is missing or identical across two requests",
        )
    )
    out.append(
        verdict(
            "headers.csp_no_unsafe_inline_script",
            bool(script) and "'unsafe-inline'" not in script.group(1),
            "no unsafe-inline for scripts",
            "script-src is missing or allows 'unsafe-inline'",
        )
    )
    out.append(
        verdict(
            "headers.csp_nonce_in_page",
            bool(n1) and f'nonce="{n1}"' in page.text(),
            "the nonce in the header is the one stamped on the page's scripts",
            "the nonce in the Content-Security-Policy header does not appear in the HTML (a proxy altered a header or the page)",
        )
    )
    for label, r in (("web", page), ("api", api)):
        csp = r.header("Content-Security-Policy") or ""
        frame = (r.header("X-Frame-Options") or "").upper() == "DENY" or "frame-ancestors 'none'" in csp
        problems = []
        if (r.header("X-Content-Type-Options") or "").lower() != "nosniff":
            problems.append("no nosniff")
        if not frame:
            problems.append("framing allowed")
        if label == "api" and "no-store" not in (r.header("Cache-Control") or "").lower():
            problems.append("API response is cacheable")
        if r.header("X-Powered-By"):
            problems.append("X-Powered-By present")
        if any(re.search(r"\d", v) for v in r.all("Server")):
            problems.append("Server header names a version")
        out.append(
            verdict(
                f"headers.hardening.{label}",
                not problems,
                "nosniff, frame denial" + (", no-store" if label == "api" else "") + ", no version leakage",
                "; ".join(problems),
            )
        )
    return out


def check_closed_paths(t: Target, o: Options) -> List[Result]:
    out: List[Result] = []
    bad_paths = []
    for path in ("/docs", "/redoc", "/openapi.json", "/readyz"):
        r = t.request("GET", path)
        if r.status != 404:
            bad_paths.append(f"{path} -> {r.status}")
    out.append(
        verdict(
            "paths.docs_readyz_closed",
            not bad_paths,
            "/docs, /redoc, /openapi.json and /readyz answer 404 publicly",
            "reachable from the public origin: " + ", ".join(bad_paths),
        )
    )
    attempts = [({}, "no credential"), ({"Authorization": "Bearer " + _random_key()}, "a made-up token")]
    if o.metrics_token:
        attempts.append(({"Authorization": "Bearer " + o.metrics_token}, "the real token"))
    failed = []
    for headers, label in attempts:
        r = t.request("GET", "/metrics", headers)
        if r.status != 404:
            failed.append(f"{label}: {r.status}")
    out.append(
        verdict(
            "paths.metrics_not_public",
            not failed,
            "/metrics answers 404 with " + ("and without the token" if o.metrics_token else "no credential and a made-up token"),
            "/metrics is reachable from the public origin (" + "; ".join(failed) + ")",
        )
    )
    if not o.metrics_token:
        out.append(skip("paths.metrics_with_real_token", "no --metrics-token-file given"))
    return out


def check_health_and_cookie(t: Target) -> List[Result]:
    out: List[Result] = []
    r = t.request("GET", "/healthz")
    healthy = r.status == 200 and '"ok"' in r.text()
    out.append(verdict("health.healthz", healthy, "200 ok", f"/healthz answered {r.status}"))
    me = t.request("GET", "/v1/me")
    out.append(verdict("auth.me_unauthenticated_401", me.status == 401, "401 without a cookie", f"answered {me.status}"))
    start = t.request("GET", "/api/auth/login", attempts=2)
    cookies = [c for c in start.all("Set-Cookie") if c.split("=", 1)[0].strip() == "__Host-abb_login"]
    if start.status not in (302, 303, 307) or not cookies:
        out.append(
            bad(
                "auth.login_cookie",
                f"sign-in start answered {start.status} and set no __Host-abb_login cookie (is the identity provider reachable from the API?)",
            )
        )
        return out
    problems = _cookie_problems([a.strip().lower() for a in cookies[0].split(";")[1:]])
    out.append(
        verdict(
            "auth.login_cookie",
            not problems,
            "__Host-abb_login is Secure, HttpOnly, Path=/, no Domain, SameSite set",
            "__Host-abb_login: " + ", ".join(problems),
        )
    )
    return out


def _cookies(r: Response) -> Dict[str, List[str]]:
    """Set-Cookie lines by cookie name: [value, attribute, ...] with attributes lower-cased."""
    out: Dict[str, List[str]] = {}
    for line in r.all("Set-Cookie"):
        parts = [x.strip() for x in line.split(";")]
        name, _, value = parts[0].partition("=")
        out[name.strip()] = [value] + [a.lower() for a in parts[1:]]
    return out


def _cookie_problems(attrs: List[str]) -> List[str]:
    problems = []
    if "secure" not in attrs:
        problems.append("no Secure")
    if "httponly" not in attrs:
        problems.append("no HttpOnly")
    if "path=/" not in attrs:
        problems.append("Path is not /")
    if any(a.startswith("domain=") for a in attrs):
        problems.append("has a Domain attribute")
    if not any(a.startswith("samesite=") for a in attrs):
        problems.append("no SameSite")
    return problems


def _location_target(t: Target, location: str) -> Tuple[Target, str]:
    """The Target and request path of an absolute Location (the identity provider or the callback)."""
    parts = urlsplit(location)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    if parts.hostname == t.host and port == t.port:
        return t, path
    return Target(f"{parts.scheme}://{parts.hostname}:{port}", t.cacert, t.resolve, t.http_port, t.timeout), path


def _sign_in(t: Target, email: str) -> Tuple[Optional[Response], str]:
    """Run the authorization-code flow against a provider that accepts any email. Returns the callback response and a note."""
    start = t.request("GET", "/api/auth/login", attempts=1)
    login = _cookies(start).get("__Host-abb_login")
    location = start.header("Location") or ""
    if start.status not in (302, 303, 307) or not login or not location.startswith("http"):
        return None, f"sign-in start answered {start.status} without a provider redirect"
    idp, authorize_path = _location_target(t, location)
    page = idp.request("GET", authorize_path)
    if page.status != 200:
        return None, f"the provider's sign-in page answered {page.status}"
    fields = dict(
        (html.unescape(k), html.unescape(v))
        for k, v in re.findall(r'<input type="hidden" name="([^"]*)" value="([^"]*)">', page.text())
    )
    fields["email"] = email
    posted = idp.request(
        "POST", authorize_path.split("?")[0],
        {"Content-Type": "application/x-www-form-urlencoded"}, urlencode(fields).encode(), attempts=1,
    )  # fmt: skip
    back = posted.header("Location") or ""
    if posted.status not in (302, 303) or not back.startswith("http"):
        return None, f"the provider did not redirect back (answered {posted.status})"
    app, callback_path = _location_target(t, back)
    done = app.request("GET", callback_path, {"Cookie": "__Host-abb_login=" + login[0]}, attempts=1)
    return done, ""


def _check_relay(t: Target, o: Options, cookie: str) -> List[Result]:
    """What a browser does after signing in: it talks to the web server only, which relays to the API (ADR-021, ADR-060).

    The relay forwards the session cookie and drops X-Forwarded-*, so the API sees the web container as the client address
    for browser traffic (open question U2). These checks show the consequences: the stream still flows through two proxies, and a
    flood of junk session cookies at the relay does not lock out a person whose session is valid.
    """
    out: List[Result] = []
    me = t.request("GET", "/api/abb/v1/me", {"Cookie": cookie})
    out.append(verdict("relay.me_through_web", me.status == 200, "the dashboard's relay answers with a valid session", f"/api/abb/v1/me answered {me.status}"))
    if o.stream_path:
        try:
            workspace = json.loads(me.body or b"{}")["memberships"][0]["workspace"]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            workspace = ""
        headers = {"Cookie": cookie, "X-ABB-Workspace": workspace} if workspace else {"Cookie": cookie}
        probe = probe_stream(t, "/api/abb" + o.stream_path, headers, o.first_bound, o.heartbeat_bound)
        arrivals = probe.arrivals
        first = arrivals[0][0] if arrivals else None
        later = [a for a in arrivals[1:] if first is not None and a[0] - first > 0.2]
        good = probe.status == 200 and first is not None and first <= o.first_bound and bool(later)
        out.append(
            verdict(
                "relay.stream_through_web",
                good,
                f"the live stream flows through the proxy and the web relay (first bytes {first:.1f}s, a later chunk {later[0][0]:.1f}s)" if good else "",
                f"stream through the relay: status {probe.status}, {len(arrivals)} chunk(s), error {probe.error or 'none'}",
            )
        )
    # Past the API's global ceiling for failed sessions: the relay hides the client address, so this is the only limit that
    # binds a flood sent through it (U2). Valid sessions must still be served while it is closed.
    junk_codes = set()
    for _ in range(o.session_global + 20):
        junk = t.request("GET", "/api/abb/v1/me", {"Cookie": "__Host-abb_session=" + secrets.token_urlsafe(32)}, attempts=1)
        junk_codes.add(junk.status)
    after = t.request("GET", "/api/abb/v1/me", {"Cookie": cookie})
    out.append(
        verdict(
            "relay.junk_flood_keeps_valid_session",
            after.status == 200 and 401 in junk_codes,
            f"after {o.session_global + 20} junk session cookies through the relay (answers {sorted(junk_codes)}; "
            f"global ceiling {'reached' if 429 in junk_codes else 'not reached'}) a valid session still works",
            f"a valid session answered {after.status} after a flood of junk cookies at the relay (answers {sorted(junk_codes)})",
        )
    )
    return out


def check_signin(t: Target, o: Options) -> List[Result]:
    """Sign in over https through the proxy with a provider that accepts any email (the rig's fake issuer)."""
    out: List[Result] = []
    if o.signin_email:
        done, note = _sign_in(t, o.signin_email)
        session = _cookies(done).get("__Host-abb_session") if done else None
        if done is None or not session or not session[0]:
            where = (done.header("Location") or "") if done else ""
            out.append(bad("signin.invited_person", note or f"no __Host-abb_session cookie was issued (callback answered {done.status if done else '?'} to {where.split('?')[0]})"))
        else:
            problems = _cookie_problems(session[1:])
            me = t.request("GET", "/v1/me", {"Cookie": "__Host-abb_session=" + session[0]})
            if me.status != 200:
                problems.append(f"the session was refused by the API ({me.status})")
            out.append(
                verdict(
                    "signin.invited_person",
                    not problems,
                    "signed in over https; __Host-abb_session is Secure, HttpOnly, Path=/, no Domain, and the API accepts it",
                    "; ".join(problems),
                )
            )
    if o.signin_email and out and out[-1].status == "PASS" and session:
        out.extend(_check_relay(t, o, "__Host-abb_session=" + session[0]))
    if o.signin_stranger:
        done, note = _sign_in(t, o.signin_stranger)
        session = _cookies(done).get("__Host-abb_session") if done else None
        refused = done is not None and not (session and session[0]) and "error=" in (done.header("Location") or "")
        out.append(
            verdict(
                "signin.stranger_refused",
                refused,
                "a person nobody invited gets no session and is sent back to the login page with an error",
                note or "a person nobody invited was given a session or no error redirect",
            )
        )
    return out


def check_forwarded_for(t: Target, o: Options) -> List[Result]:
    """Rotating forged X-Forwarded-For values must still hit one per-address budget.

    Each request carries a different made-up key id (so the per-key budget never trips) and a different
    forged client address in the first position. If the proxy replaced the header and ABB_TRUST_PROXY=1,
    all of them are one address and the API answers 429 AUTH_THROTTLED after its budget. If the forged
    value were trusted, or the proxy trust were off, no request would ever be throttled.
    """
    seen_429: Optional[Response] = None
    sent = 0
    for sent in range(1, o.api_budget + 26):  # noqa: B007 - `sent` is also the count reported below
        r = t.request(
            "GET",
            "/v1/runs",
            {"Authorization": "Bearer " + _random_key(), "X-Forwarded-For": _random_ip()},
            attempts=1,
        )
        if r.status == 429:
            seen_429 = r
            break
    out = [
        verdict(
            "xff.forged_header_cannot_evade_throttle",
            seen_429 is not None,
            f"throttled after {sent} attempts despite a new forged X-Forwarded-For on each",
            f"{sent} failed attempts with rotating forged X-Forwarded-For were never throttled "
            "(the proxy passes the forged header, or ABB_TRUST_PROXY is not 1 on the api)",
        )
    ]
    out.append(
        verdict(
            "xff.throttle_has_retry_after",
            seen_429 is not None and (seen_429.header("Retry-After") or "").isdigit(),
            "429 carries Retry-After",
            "no 429 with a numeric Retry-After was seen",
        )
    )
    return out


def check_flood(t: Target, o: Options) -> List[Result]:
    """Anonymous sign-in starts are limited per address; health stays green meanwhile."""
    limited: Optional[Response] = None
    health_codes = []
    for n in range(1, o.web_budget + 16):
        r = t.request("GET", "/api/auth/login", attempts=1)
        if (r.header("Retry-After") or "").isdigit() and limited is None:
            limited = r
        if n % 5 == 0:
            health_codes.append(t.request("GET", "/healthz").status)
        if limited is not None and n > 5 and len(health_codes) >= 2:
            break
    health_codes.append(t.request("GET", "/healthz").status)
    return [
        verdict(
            "flood.signin_start_limited",
            limited is not None,
            "sign-in starts are refused with Retry-After once the per-address budget is spent",
            f"{o.web_budget + 15} sign-in starts from one address were never refused (ABB_TRUST_PROXY missing on web, or the limiter is off)",
        ),
        verdict(
            "flood.health_unaffected",
            all(code == 200 for code in health_codes),
            "/healthz stayed 200 during the flood",
            f"/healthz answered {sorted(set(health_codes))} during the flood",
        ),
    ]


def check_stream(t: Target, o: Options) -> List[Result]:
    if not o.stream_path or not o.stream_token:
        return [skip("sse.first_event", "no --stream-path / --stream-token-file given")]
    probe = probe_stream(
        t, o.stream_path, {"Authorization": "Bearer " + o.stream_token}, o.first_bound, o.heartbeat_bound
    )
    if probe.error or probe.status != 200 or not probe.arrivals:
        why = probe.error or f"status {probe.status}" if (probe.error or probe.status != 200) else "no bytes arrived"
        return [bad("sse.first_event", f"the event stream did not open: {why}")]
    first = probe.arrivals[0][0]
    out = [
        verdict(
            "sse.first_event",
            first <= o.first_bound,
            f"first bytes arrived while the stream was open ({first:.1f}s, bound {o.first_bound:.0f}s)",
            f"first bytes took {first:.1f}s (bound {o.first_bound:.0f}s): the proxy is buffering the stream",
        )
    ]
    later = [a for a in probe.arrivals[1:] if a[0] - first > 0.2]
    out.append(
        verdict(
            "sse.heartbeat",
            bool(later),
            f"a later chunk arrived on the same open stream ({later[0][0]:.1f}s)" if later else "",
            f"nothing more arrived within {o.first_bound + o.heartbeat_bound:.0f}s: the keep-alive never came through (buffering or an idle timeout)",
        )
    )
    encoding = probe.header("Content-Encoding")
    ctype = (probe.header("Content-Type") or "").lower()
    out.append(
        verdict(
            "sse.not_compressed",
            not encoding and ctype.startswith("text/event-stream"),
            "text/event-stream, no Content-Encoding",
            f"Content-Type {ctype!r}, Content-Encoding {encoding!r}: the proxy compresses the event stream (clients and caches may then buffer it)",
        )
    )
    return out


def check_body_limit(t: Target, o: Options) -> List[Result]:
    out: List[Result] = []
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + _random_key()}
    big = t.request("POST", "/v1/events/batch", headers, b"0" * o.over_limit_bytes)
    # No valid credential: the API never reads the body, so a 413 here is the proxy's.
    api_json = big.text().lstrip().startswith("{") and "PAYLOAD_TOO_LARGE" in big.text()
    out.append(
        verdict(
            "body.over_limit_refused_413",
            big.status == 413,
            f"{o.over_limit_bytes // 1048576} MiB refused with 413" + (" (by the API)" if api_json else " (by the proxy)"),
            f"{o.over_limit_bytes // 1048576} MiB answered {big.status}, want 413",
        )
    )
    mid = t.request("POST", "/v1/events/batch", headers, b"{" + b" " * o.under_limit_bytes + b"}")
    out.append(
        verdict(
            "body.under_limit_reaches_api",
            mid.status != 413 and mid.status in (401, 403, 422, 429),
            f"{o.under_limit_bytes // 1048576} MiB passes the proxy (answered {mid.status} by the API)",
            f"{o.under_limit_bytes // 1048576} MiB answered {mid.status}: the proxy limit is lower than the API's",
        )
    )
    small = t.request("POST", "/v1/events/batch", headers, json.dumps({"events": []}).encode().ljust(1048576, b" "))
    json_ct = (small.header("Content-Type") or "").startswith("application/json")
    out.append(
        verdict(
            "body.ingest_path_reaches_api",
            small.status in (401, 403, 422, 429) and json_ct,
            f"a 1 MiB ingest request reaches the API (answered {small.status})",
            f"a 1 MiB ingest request answered {small.status} (content-type {small.header('Content-Type')!r}): the path does not reach the API",
        )
    )
    return out


def check_compression(t: Target) -> List[Result]:
    page = t.request("GET", "/", {"Accept": "text/html"})
    m = re.search(r'(?:src|href)="(/_next/static/[^"]+\.(?:js|css))"', page.text())
    if not m:
        return [bad("compress.static_asset", "no /_next/static asset is referenced by the home page")]
    r = t.request("GET", m.group(1), {"Accept-Encoding": "gzip"})
    enc = (r.header("Content-Encoding") or "").lower()
    return [
        verdict(
            "compress.static_asset",
            r.status == 200 and enc in ("gzip", "zstd", "br"),
            f"a text asset is served {enc}-encoded",
            f"asset answered {r.status} with Content-Encoding {enc!r}",
        )
    ]


# --------------------------------------------------------------------------- compose checks


def _services(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return config.get("services", {}) or {}


def _env(service: Dict[str, Any]) -> Dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if "=" in item)
    return {k: ("" if v is None else str(v)) for k, v in env.items()}


def check_rendered_config(config: Dict[str, Any]) -> List[Result]:
    """Checks on the output of `docker compose config --format json` for the production overlay (A1)."""
    out: List[Result] = []
    services = _services(config)
    published = []
    for name, svc in services.items():
        for port in svc.get("ports") or []:
            host_ip = port.get("host_ip") if isinstance(port, dict) else None
            if name in UNPUBLISHED or host_ip not in LOOPBACK:
                published.append(f"{name}:{port.get('published') if isinstance(port, dict) else port}")
    out.append(
        verdict(
            "config.no_published_ports",
            not published,
            "no application service publishes a port; dashboards (if any) are loopback only",
            "published ports: " + ", ".join(published),
        )
    )
    wrong = [
        n for n in ("migrate", "api", "worker", "retention", "backup") if n in services and _env(services[n]).get("ABB_ENVIRONMENT") != "production"
    ]
    missing = [n for n in ("migrate", "api", "worker", "retention") if n not in services]
    out.append(
        verdict(
            "config.environment_production",
            not wrong and not missing,
            "ABB_ENVIRONMENT=production on migrate, api, worker, retention (and backup)",
            f"not production: {wrong}; not rendered: {missing}",
        )
    )
    out.append(
        verdict(
            "config.no_fake_oidc",
            "fake-oidc" not in services,
            "the development sign-in provider is not part of the default profiles",
            "fake-oidc would be started",
        )
    )
    leaked = sorted(
        {
            f"{n}:{k}"
            for n, s in services.items()
            for k, v in _env(s).items()
            if any(d in v for d in DEV_DEFAULTS)
        }
    )
    out.append(
        verdict(
            "config.no_development_secrets",
            not leaked,
            "no development default password in any service environment",
            "development defaults in: " + ", ".join(leaked),
        )
    )
    api, web = _env(services.get("api", {})), _env(services.get("web", {}))
    problems = []
    if api.get("ABB_TRUST_PROXY") != "1" or web.get("ABB_TRUST_PROXY") != "1":
        problems.append("ABB_TRUST_PROXY is not 1 on api and web")
    for label, env in (("api", api), ("web", web)):
        if not env.get("ABB_WEB_ORIGIN", "").startswith("https://"):
            problems.append(f"{label}: ABB_WEB_ORIGIN is not https")
    if api.get("ABB_ALLOW_DEV_SESSIONS") not in (None, "", "0", "false"):
        problems.append("ABB_ALLOW_DEV_SESSIONS is enabled")
    if not api.get("ABB_OIDC_ISSUER", "").startswith("https://"):
        problems.append("ABB_OIDC_ISSUER is not https")
    if api.get("ABB_CORS_ORIGINS") != api.get("ABB_WEB_ORIGIN"):
        problems.append("ABB_CORS_ORIGINS differs from ABB_WEB_ORIGIN")
    out.append(
        verdict(
            "config.proxy_and_origin",
            not problems,
            "ABB_TRUST_PROXY=1, https origin and issuer, CORS equals the origin, dev sessions off",
            "; ".join(problems),
        )
    )
    nets = []
    for n in ("api", "web"):
        if "edge" not in (services.get(n, {}).get("networks") or {}):
            nets.append(f"{n} is not on the edge network")
    if "edge" in (services.get("postgres", {}).get("networks") or {}):
        nets.append("postgres is on the edge network")
    out.append(verdict("config.edge_network", not nets, "api and web are on edge, postgres is not", "; ".join(nets)))
    ceilings = [n for n in ("postgres", "api", "worker", "retention", "web") if not services.get(n, {}).get("mem_limit")]
    restarts = [n for n in ("postgres", "api", "worker", "retention", "web") if services.get(n, {}).get("restart") != "unless-stopped"]
    out.append(
        verdict(
            "config.limits_and_restart",
            not ceilings and not restarts,
            "memory limits and restart: unless-stopped on every long-running service",
            f"no mem_limit: {ceilings}; no restart policy: {restarts}",
        )
    )
    return out


def render_config(files: Sequence[str], env_file: Optional[str], profiles: Sequence[str], docker: str = "docker") -> Dict[str, Any]:
    cmd = [docker, "compose"]
    if env_file:
        cmd += ["--env-file", env_file]
    for f in files:
        cmd += ["-f", f]
    for p in profiles:
        cmd += ["--profile", p]
    cmd += ["config", "--format", "json"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
    if done.returncode != 0:
        raise RuntimeError("docker compose config failed: " + done.stderr.strip().splitlines()[-1][:200] if done.stderr else "docker compose config failed")
    return json.loads(done.stdout)


def check_published_ports(inspected: List[Dict[str, Any]]) -> List[Result]:
    """No running container of the project has a host binding other than loopback (docker inspect JSON)."""
    exposed = []
    for c in inspected:
        name = (c.get("Name") or "?").lstrip("/")
        ports = (c.get("NetworkSettings") or {}).get("Ports") or {}
        for container_port, bindings in ports.items():
            for b in bindings or []:
                if b.get("HostIp") not in LOOPBACK:
                    exposed.append(f"{name} {container_port} on {b.get('HostIp') or 'all addresses'}:{b.get('HostPort')}")
        if (c.get("HostConfig") or {}).get("NetworkMode") == "host":
            exposed.append(f"{name} uses the host network")
    return [
        verdict(
            "ports.none_published",
            bool(inspected) and not exposed,
            f"{len(inspected)} container(s), no host binding other than loopback",
            "published to the host: " + "; ".join(exposed) if exposed else "no containers found for the project",
        )
    ]


def docker_ports(project: str, docker: str = "docker") -> List[Result]:
    ids = subprocess.run(  # noqa: S603
        [docker, "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True, text=True, timeout=60,
    ).stdout.split()  # fmt: skip
    if not ids:
        return check_published_ports([])
    raw = subprocess.run([docker, "inspect", *ids], capture_output=True, text=True, timeout=60).stdout  # noqa: S603
    return check_published_ports(json.loads(raw))


# --------------------------------------------------------------------------- runner


def run_checks(t: Target, o: Options) -> List[Result]:
    results: List[Result] = []
    groups: List[Tuple[str, Callable[[], List[Result]]]] = [
        ("tls", lambda: check_tls(t)),
        ("headers", lambda: check_headers(t)),
        ("paths", lambda: check_closed_paths(t, o)),
        ("health", lambda: check_health_and_cookie(t)),
        ("compress", lambda: check_compression(t)),
        ("body", lambda: check_body_limit(t, o)),
        ("sse", lambda: check_stream(t, o)),
    ]
    if o.signin_email or o.signin_stranger:
        groups.append(("signin", lambda: check_signin(t, o)))
    if o.flood:  # last: they spend this address's budgets
        groups += [("xff", lambda: check_forwarded_for(t, o)), ("flood", lambda: check_flood(t, o))]
    for name, fn in groups:
        try:
            results.extend(fn())
        except ssl.SSLCertVerificationError as exc:
            results.append(bad(f"{name}.error", f"certificate does not verify: {exc.verify_message}"))
        except Exception as exc:  # noqa: BLE001 - one broken group must not hide the others
            results.append(bad(f"{name}.error", f"{type(exc).__name__}: {str(exc)[:120]}"))
    return results


def summarize(results: Sequence[Result], strict: bool) -> int:
    for r in results:
        print(r.line())
    fails = sum(1 for r in results if r.status == "FAIL")
    skips = sum(1 for r in results if r.status == "SKIP")
    passes = sum(1 for r in results if r.status == "PASS")
    print(f"HOSTING-VERIFY: {passes} passed, {fails} failed, {skips} skipped")
    if fails or (strict and skips):
        return 1
    return 0


def _read_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Verify a hosted Agent BlackBox behind its reverse proxy.")
    p.add_argument("base_url", nargs="?", help="https://abb.example.org")
    p.add_argument("--cacert", help="PEM file of the CA that signed the certificate (local rig)")
    p.add_argument("--resolve", help="connect to this address instead of resolving the host name")
    p.add_argument("--http-port", type=int, default=80, help="port of the plain-http listener (redirect check)")
    p.add_argument("--metrics-token-file", help="file holding ABB_METRICS_TOKEN (never printed)")
    p.add_argument("--stream-path", help="path of a live run stream, /v1/runs/<id>/stream")
    p.add_argument("--stream-token-file", help="file holding an API key that may read that run (never printed)")
    p.add_argument("--first-bound", type=float, default=10.0)
    p.add_argument("--heartbeat-bound", type=float, default=40.0)
    p.add_argument("--web-budget", type=int, default=10)
    p.add_argument("--api-budget", type=int, default=30)
    p.add_argument("--session-global", type=int, default=600, help="ABB_SESSION_FAIL_GLOBAL of the server")
    p.add_argument("--signin-email", help="sign in as this invited person through a provider that accepts any email (the rig)")
    p.add_argument("--signin-stranger-email", help="a person nobody invited: must be refused")
    p.add_argument("--no-flood", action="store_true", help="skip the checks that spend this address's rate-limit budgets")
    p.add_argument("--strict", action="store_true", help="a SKIP is a failure")
    p.add_argument("--config-only", action="store_true", help="check the rendered production compose files, no network")
    p.add_argument("--compose-file", action="append", help="compose file(s) for --config-only (default: base + production overlay)")
    p.add_argument("--env-file", help="env file for --config-only (names and test values; secrets are not printed)")
    p.add_argument("--docker-project", help="check the published ports of this running compose project")
    p.add_argument("--docker-bin", default="docker")
    p.add_argument("--server", action="store_true", help="host-level checks, run ON the server as root (ufw, sshd, listeners, disk, backup...)")
    p.add_argument("--server-probe", metavar="HOST", help="run from ANOTHER machine: 80/443 answer and the private ports do not")
    p.add_argument("--abb-dir", default="/opt/abb", help="--server: the checkout holding .env, secrets/ and backups/")
    p.add_argument("--deploy-user", default="deploy", help="--server: the user that must own the secrets")
    p.add_argument("--textfile-dir", default="/var/lib/node_exporter/textfile", help="--server: where abb_backup.prom is written")
    p.add_argument("--backup-pass", default="/etc/abb/backup.pass")
    p.add_argument("--http3", action="store_true", help="--server: UDP 443 is expected to be open")
    p.add_argument("--disk-limit", type=int, default=80, help="--server: percent of disk or inodes that fails the check")
    args = p.parse_args(argv)

    results: List[Result] = []
    if args.config_only:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = args.compose_file or [
            os.path.join(root, "docker-compose.yml"),
            os.path.join(root, "deploy", "hosting", "compose.production.yml"),
        ]
        try:
            results += check_rendered_config(render_config(files, args.env_file, ["app", "ops"], args.docker_bin))
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            results.append(bad("config.render", str(exc)[:200]))
    if args.docker_project:
        results += docker_ports(args.docker_project, args.docker_bin)
    if args.server or args.server_probe:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import hosting_server_checks as server  # noqa: E402 - imports this module, so it is loaded lazily

        if args.server:
            results += server.run_server_checks(
                server.Host(),
                server.Settings(args.abb_dir, args.deploy_user, args.textfile_dir, args.backup_pass, args.http3, args.disk_limit),
            )
        if args.server_probe:
            results += server.probe_from_outside(args.server_probe)
    if args.base_url:
        try:
            target = Target(args.base_url, args.cacert, args.resolve, args.http_port)
        except ValueError as exc:
            print(f"hosting-verify: {exc}", file=sys.stderr)
            return 2
        options = Options(
            metrics_token=_read_file(args.metrics_token_file),
            stream_path=args.stream_path,
            stream_token=_read_file(args.stream_token_file),
            web_budget=args.web_budget,
            api_budget=args.api_budget,
            session_global=args.session_global,
            first_bound=args.first_bound,
            heartbeat_bound=args.heartbeat_bound,
            flood=not args.no_flood,
            signin_email=args.signin_email,
            signin_stranger=args.signin_stranger_email,
        )
        results += run_checks(target, options)
    if not results:
        p.error("give a base URL, --config-only, --docker-project, --server or --server-probe")
    return summarize(results, args.strict)


if __name__ == "__main__":
    sys.exit(main())
