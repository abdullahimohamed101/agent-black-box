"""The https path of the policy transport, against a local server with a throwaway certificate."""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_blackbox import BlackBox, PolicyDenied, PolicyUnavailable
from tests.policy_server import PolicyServer, deny

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[str, str]:
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(  # noqa: S603  (a throwaway certificate for 127.0.0.1)
        [  # noqa: S607
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return str(cert), str(key)


@pytest.fixture
def secure(certificate: tuple[str, str]) -> Iterator[PolicyServer]:
    server = PolicyServer(tls=certificate)
    yield server
    server.stop()


def client(url: str, **options: Any) -> BlackBox:
    return BlackBox(
        api_key="abb_live_test.secret", endpoint=url, mode="http", policy_mode="enforce",
        policy_timeout=1.0, **options,
    )  # fmt: skip


def test_decisions_work_over_https_when_the_certificate_is_trusted(
    secure: PolicyServer, certificate: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", certificate[0])
    secure.queue("decide", deny("no"))
    bb = client(secure.url)
    with bb.run("t"):
        with pytest.raises(PolicyDenied):
            bb.guard("a.b", "x", "R3")
        assert bb.guard("a.b", "x", "R3").via == "policy"  # the next answer is the default allow
    assert len(secure.of("decide")) == 2
    bb.shutdown(1)


def test_an_untrusted_certificate_is_an_outage_not_a_bypass(
    secure: PolicyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    bb = client(secure.url)
    with bb.run("t"):
        assert bb.guard("a.b", "x", "R0").via == "fail_open"  # warned
        with pytest.raises(PolicyUnavailable) as info:
            bb.guard("a.b", "x", "R3")
    assert info.value.reason == "policy_service_unreachable" and secure.of("decide") == []
    bb.shutdown(0.2)
