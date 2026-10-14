"""The test-only network of the alert sender (ADR-151 addendum): off by default, refused outside
development and test, and when on it changes only the resolver, the connect address and the trust
anchor; the production address check still judges every answer."""

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from abb_api.alerts import notifiers as n
from abb_api.alerts import outbound as o
from abb_api.alerts.testnet import sender_from_file
from abb_api.core.config import Settings
from tests.alerts_receivers import HOST, PUBLIC_IP, TlsReceiver

URL = "postgresql+asyncpg://x:y@localhost/z"


def test_the_test_network_is_off_by_default() -> None:
    assert Settings(database_url=URL).alerts_test_network_file is None
    assert Settings(database_url=URL, environment="production").alerts_test_network_file is None


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_it_cannot_be_enabled_outside_development_and_test(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(ValidationError, match="only for development or test"):
        Settings(
            database_url=URL,
            environment=environment,
            alerts_test_network_file=Path("/tmp/x.json"),  # noqa: S108
        )
    for local in ("development", "test"):
        Settings(
            database_url=URL,
            environment=local,
            alerts_test_network_file=Path("/tmp/x.json"),  # noqa: S108
        )


def test_the_default_registry_does_not_use_it() -> None:
    settings = Settings(database_url=URL, environment="test")
    assert n._default_sender(settings)._resolver is o.system_resolver


async def test_names_resolve_from_the_file_and_the_address_check_still_applies(
    tmp_path: Path,
) -> None:
    async with TlsReceiver() as receiver:
        ca = tmp_path / "ca.pem"
        ca.write_bytes(receiver.cert)
        table = tmp_path / "net.json"

        def write(**hosts: list[str]) -> None:
            table.write_text(json.dumps({"hosts": hosts, "ca_pem": str(ca)}))

        write(**{HOST: [PUBLIC_IP]})
        sender = sender_from_file(table, timeout_seconds=3)
        url = f"https://{HOST}:{receiver.port}/hook"
        assert (await sender.post(url, {}, b"{}")).ok
        assert len(receiver.requests) == 1
        write(**{HOST: ["10.0.0.5"]})  # the same name now answers privately: refused, no request
        refused = await sender.post(url, {}, b"{}")
        assert refused.error_code == o.BLOCKED_ADDRESS and len(receiver.requests) == 1
        write(**{HOST: ["::1"]})
        assert (await sender.post(url, {}, b"{}")).error_code == o.BLOCKED_ADDRESS
        other = await sender.post(f"https://unknown.example.test:{receiver.port}/", {}, b"{}")
        assert other.error_code == o.DNS_FAILED  # never falls back to the system resolver
        assert len(receiver.requests) == 1
