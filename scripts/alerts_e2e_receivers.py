"""Local receivers of scripts/alerts-e2e.sh (Phase 17 step 4). Run with `uv run --project apps/api python`.

    alerts_e2e_receivers.py gencert DIR NAME...          a self-signed certificate (cert.pem, key.pem) valid for the names
    alerts_e2e_receivers.py serve DIR CTRL MAIN SLACK FLAKY SMTP

One process, four listeners and a control port (plain HTTP on 127.0.0.1):

* MAIN and FLAKY: TLS webhook receivers. They VERIFY the signature (HMAC-SHA256 over "timestamp.body" with the secret registered for
  the path) and the timestamp (within five minutes), and record every request: path key, alert id, attempt, validity, the body.
* SLACK: a TLS receiver that records Slack-shaped bodies.
* SMTP: a plain SMTP sink (STARTTLS is not offered; the script points ABB_SMTP_* at it with ABB_SMTP_STARTTLS=false).
* Control: GET /state, POST /mode {key, mode: ok|500|429|400|302|slow}, POST /listener {name, up}, POST /secret {key, secret}.
  A "down" receiver is a listener that is really stopped (connection refused), not one that answers an error.

The receivers keep everything in memory and write nothing to disk but the certificate the script asked for.
"""

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import ssl
import sys
import time
from email import message_from_bytes
from pathlib import Path
from typing import Any

SLOW_SECONDS = 6.0
TOLERANCE_SECONDS = 300


def gencert(directory: Path, names: list[str]) -> None:
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=6))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "key.pem").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    (directory / "key.pem").chmod(0o600)


class State:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mail: list[dict[str, Any]] = []
        self.modes: dict[str, str] = {}
        self.secrets: dict[str, str] = {}
        self.connections: dict[str, int] = {}


async def read_http(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {k.lower(): v for k, _, v in (line.partition(": ") for line in lines[1:] if line)}
    body = await reader.readexactly(int(headers.get("content-length", "0")))
    return method, path, headers, body


async def reply(writer: asyncio.StreamWriter, status: int, extra: dict[str, str] | None = None, body: bytes = b"") -> None:
    lines = [f"HTTP/1.1 {status} X", "Connection: close", f"Content-Length: {len(body)}", *(f"{k}: {v}" for k, v in (extra or {}).items())]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
    await writer.drain()


def verdict(state: State, key: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    """The signature and timestamp checks a real receiver does."""
    secret = state.secrets.get(key)
    stamp, signature = headers.get("x-abb-timestamp", ""), headers.get("x-abb-signature", "")
    out: dict[str, Any] = {"signed": bool(signature), "signature_ok": None, "timestamp_ok": None}
    try:
        out["timestamp_ok"] = abs(time.time() - int(stamp)) <= TOLERANCE_SECONDS
    except ValueError:
        out["timestamp_ok"] = False
    if secret is not None and signature:
        mac = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        out["signature_ok"] = hmac.compare_digest(signature, f"v1={mac}")
    return out


def web_handler(state: State, listener: str, main_port: int):  # type: ignore[no-untyped-def]
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        state.connections[listener] = state.connections.get(listener, 0) + 1
        try:
            _, path, headers, body = await read_http(reader)
            parts = path.split("?")[0].strip("/").split("/")
            key = parts[1] if len(parts) > 1 else ""
            record: dict[str, Any] = {
                "listener": listener, "path": path.split("?")[0], "key": key, "at": time.time(),
                "alert_id": headers.get("x-abb-alert-id"), "attempt": headers.get("x-abb-delivery-attempt"),
                "user_agent": headers.get("user-agent"), "content_type": headers.get("content-type"),
                "body": body.decode("utf-8", "replace"), "headers": sorted(headers),
                **verdict(state, key, headers, body),
            }
            state.requests.append(record)
            mode = state.modes.get(key, "ok")
            if mode == "slow":
                await asyncio.sleep(SLOW_SECONDS)
            if mode == "500":
                await reply(writer, 500)
            elif mode == "429":
                await reply(writer, 429)
            elif mode == "400":
                await reply(writer, 400)
            elif mode == "302":
                await reply(writer, 302, {"Location": f"https://hooks.example.test:{main_port}/redirected/{key}"})
            else:
                await reply(writer, 200, body=b"ok")
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, OSError):
            pass
        finally:
            writer.close()

    return handle


async def smtp_handler(state: State, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sender, rcpts = "", []

    async def say(line: str) -> None:
        writer.write(line.encode() + b"\r\n")
        await writer.drain()

    try:
        await say("220 alerts-sink ready")
        while True:
            raw = await reader.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb == "MAIL":
                sender, rcpts = line.split(":", 1)[1].strip(), []
                await say("250 OK")
            elif verb == "RCPT":
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
                msg = message_from_bytes(data)
                state.mail.append({
                    "at": time.time(), "from": sender, "to": rcpts, "subject": str(msg.get("Subject", "")),
                    "alert_id": msg.get("X-ABB-Alert-Id"), "message_id": msg.get("Message-ID"), "raw": data.decode("utf-8", "replace"),
                })
                await say("250 queued")
            elif verb == "QUIT":
                await say("221 bye")
                return
            else:
                await say("250 OK")
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def control(state: State, listeners: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, path, _, body = await read_http(reader)
        data = json.loads(body) if body else {}
        if method == "GET" and path == "/state":
            payload = json.dumps({"requests": state.requests, "mail": state.mail, "connections": state.connections}).encode()
            await reply(writer, 200, {"Content-Type": "application/json"}, payload)
        elif method == "POST" and path == "/mode":
            state.modes[data["key"]] = data["mode"]
            await reply(writer, 200)
        elif method == "POST" and path == "/secret":
            state.secrets[data["key"]] = data["secret"]
            await reply(writer, 200)
        elif method == "POST" and path == "/listener":
            await listeners[data["name"]](bool(data["up"]))
            await reply(writer, 200)
        else:
            await reply(writer, 404)
    except Exception as exc:  # the script's own control channel; say what went wrong
        await reply(writer, 500, body=type(exc).__name__.encode())
    finally:
        writer.close()


async def serve(directory: Path, ctrl: int, main: int, slack: int, flaky: int, smtp: int) -> None:
    state = State()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
    servers: dict[str, asyncio.AbstractServer | None] = {}

    def starter(name: str, port: int):  # type: ignore[no-untyped-def]
        async def toggle(up: bool) -> None:
            current = servers.get(name)
            if up and current is None:
                servers[name] = await asyncio.start_server(
                    web_handler(state, name, main), "127.0.0.1", port, ssl=context, reuse_address=True, backlog=128
                )
            elif not up and current is not None:
                current.close()
                await current.wait_closed()
                servers[name] = None

        return toggle

    listeners = {"main": starter("main", main), "slack": starter("slack", slack), "flaky": starter("flaky", flaky)}
    for toggle in listeners.values():
        await toggle(True)
    mail_server = await asyncio.start_server(lambda r, w: smtp_handler(state, r, w), "127.0.0.1", smtp, backlog=128)
    control_server = await asyncio.start_server(lambda r, w: control(state, listeners, r, w), "127.0.0.1", ctrl)
    print("receivers ready", flush=True)
    async with control_server, mail_server:
        await asyncio.Event().wait()


def main() -> None:
    command = sys.argv[1]
    if command == "gencert":
        gencert(Path(sys.argv[2]), sys.argv[3:])
    elif command == "serve":
        ctrl, main_port, slack, flaky, smtp = map(int, sys.argv[3:8])
        asyncio.run(serve(Path(sys.argv[2]), ctrl, main_port, slack, flaky, smtp))
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()
