"""Local receivers for the delivery tests: a TLS webhook server with a self-signed certificate for
a reserved name, a resolver stub and a connection opener that sends the (pinned, "public") address
the sender checked to the local port, and a tiny SMTP sink. Nothing here touches the network."""

import asyncio
import datetime as dt
import ssl
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from abb_api.alerts import outbound as o
from abb_api.alerts.notifiers import build_registry
from abb_api.core.config import Settings

HOST = "hooks.example.test"
PUBLIC_IP = "93.184.216.34"  # a public address the stub resolver hands out; nothing connects to it
RESPONSE_SENTINEL = "RESPONSEBODYSENTINEL"


def make_certificate(hostname: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def server_context(cert: bytes, key: bytes) -> ssl.SSLContext:
    with tempfile.TemporaryDirectory() as directory:
        cert_path, key_path = Path(directory) / "c.pem", Path(directory) / "k.pem"
        cert_path.write_bytes(cert)
        key_path.write_bytes(key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
    return context


@dataclass
class Request:
    path: str
    headers: dict[str, str]
    body: bytes


Responder = Callable[[Request], tuple[int, dict[str, str]]]


def ok(_: Request) -> tuple[int, dict[str, str]]:
    return 200, {}


@dataclass
class TlsReceiver:
    """A TLS HTTP server. `responder` decides each answer; requests are recorded in order."""

    hostname: str = HOST
    responder: Responder = ok
    requests: list[Request] = field(default_factory=list)
    connections: int = 0
    stall: bool = False  # accept and never answer
    flood: bool = False  # answer with an endless header line
    port: int = 0
    cert: bytes = b""
    _server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> "TlsReceiver":
        cert, key = make_certificate(self.hostname)
        self.cert = cert
        self._server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0, ssl=server_context(cert, key)
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cadata=self.cert.decode())

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            headers = {
                k.lower(): v for k, _, v in (line.partition(": ") for line in lines[1:] if line)
            }
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            request = Request(lines[0].split(" ")[1], headers, body)
            self.requests.append(request)
            if self.stall:
                await asyncio.sleep(30)
                return
            if self.flood:
                writer.write(b"HTTP/1.1 200 " + b"x" * 200_000)
                await writer.drain()
                return
            status, extra = self.responder(request)
            lines_out = [
                f"HTTP/1.1 {status} X",
                "Connection: close",
                *(f"{k}: {v}" for k, v in extra.items()),
            ]
            payload = RESPONSE_SENTINEL.encode()
            lines_out.append(f"Content-Length: {len(payload)}")
            writer.write(("\r\n".join(lines_out) + "\r\n\r\n").encode() + payload)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    def sender(
        self,
        *,
        resolver: o.Resolver | None = None,
        timeout_seconds: float = 5.0,
        opened: list[tuple[str, int, str]] | None = None,
        allowed_ports: frozenset[int] | None = None,
    ) -> o.SafeHttpsSender:
        """A sender that resolves our reserved name to a public address (a stub) and "connects" to
        that address by opening the local port instead; every pinned connection is recorded."""

        async def stub(host: str, port: int) -> list[str]:
            return [PUBLIC_IP]

        async def opener(
            ip: str, port: int, context: ssl.SSLContext, server_hostname: str
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            if opened is not None:
                opened.append((ip, port, server_hostname))
            return await asyncio.open_connection(
                "127.0.0.1", self.port, ssl=context, server_hostname=server_hostname
            )

        return o.SafeHttpsSender(
            resolver=resolver or stub,
            opener=opener,
            ssl_context=self.client_context,
            timeout_seconds=timeout_seconds,
            # the receiver listens on a random port: allowing it is what the e2e script does with
            # ABB_ALERTS_ALLOWED_PORTS, the guard itself stays the production one
            allowed_ports=frozenset({443, self.port}) if allowed_ports is None else allowed_ports,
        )

    def url(self, path: str = "/hook/SECRETPATHTOKEN1234") -> str:
        return f"https://{self.hostname}:{self.port}{path}"


def registry_for(receiver: TlsReceiver, settings: Settings, **kwargs: Any) -> Any:
    return build_registry(settings, receiver.sender(**kwargs))


# ---------------------------------------------------------------- SMTP sink


@dataclass
class SmtpSink:
    """Accepts messages and keeps them. `starttls` offers STARTTLS (self-signed, so a verifying
    client must be given the certificate)."""

    starttls_context: ssl.SSLContext | None = None
    messages: list[bytes] = field(default_factory=list)
    envelopes: list[tuple[str, list[str]]] = field(default_factory=list)
    saw_plaintext_data: bool = False
    fail_with: str | None = None  # e.g. "451 try later", "550 no"
    port: int = 0
    _server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> "SmtpSink":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tls = False
        sender = ""
        rcpts: list[str] = []

        async def say(line: str) -> None:
            writer.write(line.encode() + b"\r\n")
            await writer.drain()

        try:
            await say("220 sink ready")
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                line = raw.decode(errors="replace").rstrip("\r\n")
                verb = line.split(" ", 1)[0].upper()
                if verb == "EHLO":
                    writer.write(b"250-sink\r\n")
                    if self.starttls_context is not None and not tls:
                        writer.write(b"250-STARTTLS\r\n")
                    await say("250 OK")
                elif verb == "STARTTLS" and self.starttls_context is not None:
                    await say("220 go ahead")
                    await writer.start_tls(self.starttls_context)
                    tls = True
                elif verb == "MAIL":
                    sender, rcpts = line.split(":", 1)[1].strip(), []
                    await say("250 OK")
                elif verb == "RCPT":
                    if self.fail_with:
                        await say(self.fail_with)
                        continue
                    rcpts.append(line.split(":", 1)[1].strip())
                    await say("250 OK")
                elif verb == "DATA":
                    await say("354 end with .")
                    data = b""
                    while True:
                        chunk = await reader.readline()
                        if chunk in (b".\r\n", b""):
                            break
                        data += chunk
                    if not tls:
                        self.saw_plaintext_data = True
                    self.messages.append(data)
                    self.envelopes.append((sender, rcpts))
                    await say("250 queued")
                elif verb == "QUIT":
                    await say("221 bye")
                    return
                else:
                    await say("250 OK")
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()
