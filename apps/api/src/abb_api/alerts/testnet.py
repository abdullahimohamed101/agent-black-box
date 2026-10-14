"""A test-only network for the alert sender (ADR-151 addendum).

The end-to-end suite runs the real API and worker against receivers on this machine, but the
sender refuses loopback and plain hostnames that do not resolve to public addresses, which is the
point of it. With `ABB_ALERTS_TEST_NETWORK_FILE` set (development and test environments only; the
settings validator rejects it anywhere else and it is unset by default) the worker and the API
build their sender from a small JSON file instead of the system:

    {"hosts": {"hooks.example.test": ["93.184.216.34"]}, "ca_pem": "/path/to/self-signed.pem"}

* the resolver answers from `hosts` (the file is read on every resolution, so a test can make a
  name "rebind" from a public to a private address between saving and delivering) and fails for
  every other name; it never asks the system;
* the opener connects to 127.0.0.1 on the URL's port, but only for an address that some `hosts`
  entry hands out (so a private answer is refused by the sender before it gets here, exactly as in
  production, and nothing else can be reached through this path);
* TLS trusts only the given certificate, and still verifies it against the hostname.

Everything between the resolver and the opener (the address check, the pinning, SNI, redirects,
timeouts) is the production code path.
"""

import asyncio
import json
import logging
import ssl
from pathlib import Path

from abb_api.alerts import outbound as o

logger = logging.getLogger(__name__)

_LOOPBACK = "127.0.0.1"


def _read(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text())
    hosts = data.get("hosts", {}) if isinstance(data, dict) else {}
    return {
        str(name).lower(): [str(a) for a in addresses]
        for name, addresses in hosts.items()
        if isinstance(addresses, list)
    }


def sender_from_file(path: Path, *, timeout_seconds: float) -> o.SafeHttpsSender:
    data = json.loads(path.read_text())
    ca_pem = data.get("ca_pem") if isinstance(data, dict) else None
    context = ssl.create_default_context(cafile=str(ca_pem)) if ca_pem else None
    logger.warning("alert sender is using the test network file (development or test only)")

    async def resolver(host: str, port: int) -> list[str]:
        answers = _read(path).get(host.lower())
        if answers is None:
            raise OSError("unknown test host")
        return answers

    async def opener(
        ip: str, port: int, ssl_context: ssl.SSLContext, server_hostname: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not any(ip in answers for answers in _read(path).values()):
            raise OSError("not a test address")
        return await asyncio.open_connection(
            _LOOPBACK, port, ssl=ssl_context, server_hostname=server_hostname
        )

    return o.SafeHttpsSender(
        resolver=resolver, opener=opener, ssl_context=context, timeout_seconds=timeout_seconds
    )
