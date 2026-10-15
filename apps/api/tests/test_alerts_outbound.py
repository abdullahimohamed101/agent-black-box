"""The outbound sender (ADR-151): an SSRF matrix with a stub resolver and a pinned connection, no
redirects, bounded time and head, TLS verified against the hostname, no header injection."""

import asyncio
import ipaddress
import ssl
import time

import pytest

from abb_api.alerts import outbound as o
from tests.alerts_receivers import HOST, PUBLIC_IP, Request, TlsReceiver

HEADERS = {"Content-Type": "application/json"}

BLOCKED = [
    "127.0.0.1", "127.255.255.254", "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.254", "0.0.0.0",  # noqa: S104
    "192.0.0.1", "198.18.0.1", "224.0.0.1", "240.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
    "2002:7f00:0001::1", "2002:0a00:0001::1",  # 6to4 carrying 127.0.0.1 and 10.0.0.1
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo carrying 192.0.2.45 / client 192.0.2.45
    "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",  # NAT64 carrying 127.0.0.1 and 169.254.169.254
    "::7f00:1", "::a00:1", "::a9fe:a9fe", "::808:808",  # IPv4-compatible (::/96), deprecated: never
]  # fmt: skip


def stub(addresses: list[str]) -> o.Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        return addresses

    return resolve


@pytest.mark.parametrize("address", BLOCKED)
async def test_a_name_that_resolves_to_a_non_public_address_is_refused_before_any_connection(
    address: str,
) -> None:
    async with TlsReceiver() as receiver:
        opened: list[tuple[str, int, str]] = []
        sender = receiver.sender(resolver=stub([address]), opened=opened)
        outcome = await sender.post(receiver.url(), HEADERS, b"{}")
    assert outcome == o.SendOutcome.failure(o.BLOCKED_ADDRESS)
    assert not outcome.retryable
    assert opened == [] and receiver.connections == 0


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "::1", "10.1.2.3"])
async def test_one_bad_answer_among_public_ones_refuses_the_whole_set(address: str) -> None:
    async with TlsReceiver() as receiver:
        opened: list[tuple[str, int, str]] = []
        sender = receiver.sender(resolver=stub([PUBLIC_IP, address]), opened=opened)
        outcome = await sender.post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.BLOCKED_ADDRESS and opened == []


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1/h", "https://[::1]/h", "https://169.254.169.254/latest/meta-data/",
     "https://[::ffff:7f00:1]/h", "https://10.0.0.1:8443/h"],
)  # fmt: skip
async def test_an_ip_literal_in_a_stored_url_is_refused_even_though_save_should_have_refused_it(
    url: str,
) -> None:
    async with TlsReceiver() as receiver:
        opened: list[tuple[str, int, str]] = []
        sender = receiver.sender(opened=opened, allowed_ports=frozenset({443, 8443}))
        outcome = await sender.post(url, HEADERS, b"{}")
    assert outcome.error_code == o.BLOCKED_ADDRESS and opened == []


@pytest.mark.parametrize(
    "url", ["http://hooks.example.test/h", "ftp://hooks.example.test/h", "https:///h", "//x/h"]
)
async def test_only_https_urls_with_a_host_are_sent(url: str) -> None:
    async with TlsReceiver() as receiver:
        outcome = await receiver.sender().post(url, HEADERS, b"{}")
    assert outcome.error_code == o.CONFIG_UNREADABLE and receiver.connections == 0


async def test_a_public_literal_is_connected_to_directly() -> None:
    async with TlsReceiver(hostname="93.184.216.34") as receiver:
        # An IP literal needs an IP SAN in real life; here the point is that no resolver runs.
        calls: list[str] = []

        async def never(host: str, port: int) -> list[str]:
            calls.append(host)
            return []

        opened: list[tuple[str, int, str]] = []
        outcome = await receiver.sender(resolver=never, opened=opened).post(
            f"https://93.184.216.34:{receiver.port}/h", HEADERS, b"{}"
        )
    assert calls == [] and opened == [("93.184.216.34", receiver.port, "93.184.216.34")]
    assert outcome.error_code in (None, o.TLS_FAILED)  # no IP SAN in the test certificate


async def test_the_connection_is_pinned_to_the_checked_address_and_the_name_is_resolved_once() -> (
    None
):
    """DNS rebinding: the first answer is public, every later one is the metadata address. The
    sender resolves once, checks that answer and connects to it; a second lookup never happens, so
    the second answer can never be used."""
    answers = iter([[PUBLIC_IP], ["169.254.169.254"], ["169.254.169.254"]])
    calls = 0

    async def rebinding(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        return next(answers)

    async with TlsReceiver() as receiver:
        opened: list[tuple[str, int, str]] = []
        outcome = await receiver.sender(resolver=rebinding, opened=opened).post(
            receiver.url(), HEADERS, b"{}"
        )
    assert outcome.ok
    assert calls == 1
    assert opened == [(PUBLIC_IP, receiver.port, HOST)]  # pinned address, original name as SNI


async def test_a_name_public_at_save_but_private_at_delivery_is_refused() -> None:
    state = {"now": [PUBLIC_IP]}

    async def moving(host: str, port: int) -> list[str]:
        return state["now"]

    async with TlsReceiver() as receiver:
        sender = receiver.sender(resolver=moving)
        assert (await sender.post(receiver.url(), HEADERS, b"{}")).ok  # when it was saved
        state["now"] = ["10.0.0.7"]  # the owner re-pointed the name inward
        outcome = await sender.post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.BLOCKED_ADDRESS
    assert len(receiver.requests) == 1


async def test_a_redirect_is_never_followed() -> None:
    def redirect(_: Request) -> tuple[int, dict[str, str]]:
        return 302, {"Location": "https://169.254.169.254/latest/meta-data/"}

    async with TlsReceiver(responder=redirect) as receiver:
        opened: list[tuple[str, int, str]] = []
        outcome = await receiver.sender(opened=opened).post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.HTTP_3XX and not outcome.retryable
    assert len(opened) == 1 and receiver.connections == 1  # one connection, no second hop


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (200, None, False), (204, None, False), (400, o.HTTP_4XX, False),
        (401, o.HTTP_4XX, False), (404, o.HTTP_4XX, False), (408, o.HTTP_4XX, True),
        (429, o.HTTP_4XX, True), (301, o.HTTP_3XX, False), (500, o.HTTP_5XX, True),
        (503, o.HTTP_5XX, True),
    ],
)  # fmt: skip
async def test_statuses_map_to_fixed_codes(status: int, code: str | None, retryable: bool) -> None:
    async with TlsReceiver(responder=lambda _: (status, {})) as receiver:
        outcome = await receiver.sender().post(receiver.url(), HEADERS, b"{}")
    assert (outcome.error_code, outcome.retryable) == (code, retryable)
    assert outcome.ok == (code is None)


async def test_the_response_body_is_not_part_of_the_outcome() -> None:
    async with TlsReceiver(responder=lambda _: (500, {})) as receiver:
        outcome = await receiver.sender().post(receiver.url(), HEADERS, b"{}")
    assert "RESPONSEBODYSENTINEL" not in repr(outcome)


async def test_a_receiver_that_never_answers_times_out_within_the_deadline() -> None:
    async with TlsReceiver(stall=True) as receiver:
        started = time.monotonic()
        outcome = await receiver.sender(timeout_seconds=0.6).post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.TIMEOUT and outcome.retryable
    assert time.monotonic() - started < 3


async def test_an_endless_response_head_is_cut_off() -> None:
    async with TlsReceiver(flood=True) as receiver:
        started = time.monotonic()
        outcome = await receiver.sender().post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.BAD_RESPONSE
    assert time.monotonic() - started < 3


async def test_a_certificate_for_another_name_fails_tls_and_is_not_retried() -> None:
    async with TlsReceiver(hostname="other.example.test") as receiver:
        # The server presents a certificate for another name than the one in the URL.
        sender = receiver.sender()
        url = f"https://{HOST}:{receiver.port}/h"
        outcome = await sender.post(url, HEADERS, b"{}")
    assert outcome.error_code == o.TLS_FAILED and not outcome.retryable
    assert receiver.requests == []


async def test_an_untrusted_certificate_fails_tls() -> None:
    async with TlsReceiver() as receiver:
        default_sender = o.SafeHttpsSender(
            resolver=receiver.sender()._resolver,
            opener=receiver.sender()._opener,
            ssl_context=ssl.create_default_context(),  # does not trust the test certificate
            timeout_seconds=3,
            allowed_ports={receiver.port},
        )
        outcome = await default_sender.post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.TLS_FAILED and receiver.requests == []


async def test_a_refused_connection_is_a_retryable_connect_failure() -> None:
    async with TlsReceiver() as receiver:
        port = receiver.port
    outcome = await receiver.sender().post(f"https://{HOST}:{port}/h", HEADERS, b"{}")
    assert outcome.error_code == o.CONNECT_FAILED and outcome.retryable


async def test_a_name_that_does_not_resolve_is_a_retryable_dns_failure() -> None:
    async def nxdomain(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    async with TlsReceiver() as receiver:
        outcome = await receiver.sender(resolver=nxdomain).post(receiver.url(), HEADERS, b"{}")
    assert outcome.error_code == o.DNS_FAILED and outcome.retryable


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Evil": "a\r\nX-Injected: 1"},
        {"X-Evil": "a\nb"},
        {"X-Bad\r\nName": "v"},
        {"X-Evil": "café"},
        {"": "v"},
    ],
)
async def test_a_header_cannot_start_another_header_or_request(headers: dict[str, str]) -> None:
    async with TlsReceiver() as receiver:
        outcome = await receiver.sender().post(receiver.url(), headers, b"{}")
    assert outcome.error_code == o.CONFIG_UNREADABLE and receiver.connections == 0


async def test_the_request_is_one_post_with_host_length_and_closed_connection() -> None:
    async with TlsReceiver() as receiver:
        await receiver.sender().post(receiver.url("/a/b?x=1"), {"X-One": "1"}, b'{"a":1}')
    (request,) = receiver.requests
    assert request.path == "/a/b?x=1" and request.body == b'{"a":1}'
    assert request.headers["host"] == f"{HOST}:{receiver.port}"
    assert request.headers["content-length"] == "7"
    assert request.headers["connection"] == "close" and request.headers["x-one"] == "1"


def test_every_blocked_literal_in_the_matrix_is_really_not_public() -> None:
    from abb_api.alerts.targets import is_public_ip

    assert not any(is_public_ip(ipaddress.ip_address(a)) for a in BLOCKED)
    assert is_public_ip(ipaddress.ip_address(PUBLIC_IP))


async def test_a_task_cancelled_mid_send_does_not_leak() -> None:
    async with TlsReceiver(stall=True) as receiver:
        task = asyncio.create_task(
            receiver.sender(timeout_seconds=30).post(receiver.url(), HEADERS, b"{}")
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------- port allow-list (KI-162)


@pytest.mark.parametrize("port", [80, 8443, 22, 25, 6379, 9200, 1, 65535])
async def test_a_port_outside_the_list_is_refused_before_any_lookup_or_connection(
    port: int,
) -> None:
    looked_up: list[str] = []

    async def resolver(host: str, p: int) -> list[str]:
        looked_up.append(host)
        return [PUBLIC_IP]

    sender = o.SafeHttpsSender(resolver=resolver)  # the production default: 443 only
    outcome = await sender.post(f"https://{HOST}:{port}/h", HEADERS, b"{}")
    assert outcome == o.SendOutcome.failure(o.BLOCKED_PORT)
    assert not outcome.retryable and looked_up == []


async def test_port_zero_is_not_read_as_443() -> None:
    outcome = await o.SafeHttpsSender().post(f"https://{HOST}:0/h", HEADERS, b"{}")
    assert outcome.error_code == o.BLOCKED_PORT


async def test_the_implicit_port_is_443_and_the_list_can_be_extended() -> None:
    async with TlsReceiver() as receiver:
        refused = await receiver.sender(allowed_ports=frozenset({443})).post(
            receiver.url(), HEADERS, b"{}"
        )
        allowed = await receiver.sender(allowed_ports=frozenset({443, receiver.port})).post(
            receiver.url(), HEADERS, b"{}"
        )
    assert refused.error_code == o.BLOCKED_PORT and allowed.ok


async def test_the_address_check_still_applies_on_an_allowed_port() -> None:
    async with TlsReceiver() as receiver:
        outcome = await receiver.sender(resolver=stub(["127.0.0.1"])).post(
            receiver.url(), HEADERS, b"{}"
        )
    assert outcome.error_code == o.BLOCKED_ADDRESS
