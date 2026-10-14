"""POST /v1/alerts/destinations/{id}/test (ADR-151): a fixed test alert through the real sender,
synchronously, with `{ok, error_code}` back and nothing else; owners and admins only."""

import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts import notifiers as n
from abb_api.alerts import outbound as o
from abb_api.alerts.delivery import use_registry
from tests.alerts_helpers import SENTINEL_SIGNING, rows, seed_destination
from tests.alerts_receivers import PUBLIC_IP, RESPONSE_SENTINEL, TlsReceiver
from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.conftest import make_settings
from tests.test_runs_api import error


@pytest.fixture
async def limited(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, alerts_tests_per_minute=2)
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


class Recorder:
    def __init__(self, result: n.SendResult) -> None:
        self.result = result
        self.calls: list[tuple[n.OpenDestination, n.Alert]] = []

    async def send(self, destination: n.OpenDestination, alert: n.Alert) -> n.SendResult:
        self.calls.append((destination, alert))
        return self.result


def registry(recorder: Any) -> dict[str, Any]:
    return {"webhook": recorder, "slack": recorder, "email": recorder}


async def owner(api: Api) -> dict[str, str]:
    return await person(api, "owner@acme.test", "OWNER")


async def destination(api: Api, **kwargs: Any) -> str:
    from abb_api.ids import public_id

    return public_id(
        IdKind.DESTINATION, await seed_destination(api.engine, api.tenant.context, **kwargs)
    )


async def post_test(api: Api, headers: dict[str, str], ident: str) -> Any:
    return await api.client.post(f"/v1/alerts/destinations/{ident}/test", headers=headers)


async def test_a_test_sends_a_fixed_alert_through_the_sender_and_reports_ok(web: Api) -> None:
    ident = await destination(web)
    recorder = Recorder(n.SendResult(True))
    with use_registry(registry(recorder)):
        response = await post_test(web, await owner(web), ident)
    assert response.status_code == 200 and response.json() == {"ok": True, "error_code": None}
    ((opened, alert),) = recorder.calls
    assert opened.config["url"].startswith("https://hooks.example.com/")  # opened for the sender
    assert opened.signing_secret == SENTINEL_SIGNING
    assert alert.test and alert.payload["kind"] == "test" and alert.payload["title"]
    assert alert.payload["project"] is None and alert.payload["subject"] == {}
    assert alert.payload["link_path"] == "/w/acme/alerts"
    assert alert.alert_id.startswith("alt_") and alert.attempt == 1


async def test_a_failing_target_is_reported_by_code_never_by_what_it_answered(web: Api) -> None:
    ident = await destination(web)
    recorder = Recorder(n.SendResult(False, "http_5xx", True))
    with use_registry(registry(recorder)):
        response = await post_test(web, await owner(web), ident)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error_code": "http_5xx"}


async def test_a_real_signed_post_reaches_a_local_receiver_and_changes_no_health(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with TlsReceiver() as receiver:
        ident = await destination(web, url=receiver.url())
        settings = make_settings("postgresql+asyncpg://x:y@localhost/z", web_origin=WEB_ORIGIN)
        with use_registry(n.build_registry(settings, receiver.sender())):
            response = await post_test(web, await owner(web), ident)
    assert response.json() == {"ok": True, "error_code": None}
    (request,) = receiver.requests
    body = json.loads(request.body)
    assert body["kind"] == "test" and body["link"] == f"{WEB_ORIGIN}/w/acme/alerts"
    mac = hmac.new(
        SENTINEL_SIGNING.encode(),
        request.headers["x-abb-timestamp"].encode() + b"." + request.body,
        hashlib.sha256,
    ).hexdigest()
    assert request.headers["x-abb-signature"] == f"v1={mac}"
    assert await rows(web) == []  # a test is not an alert: no history row
    async with web.engine.connect() as conn:
        state = (
            await conn.execute(text("SELECT state, consecutive_failures FROM alert_destinations"))
        ).one()
        audit = (
            await conn.execute(
                text("SELECT details FROM audit_log WHERE action = 'alert.destination.test'")
            )
        ).scalar_one()
    assert tuple(state) == ("active", 0)
    assert audit == {"type": "webhook", "ok": True}
    for secret in (SENTINEL_SIGNING, "SECRETPATHTOKEN1234", RESPONSE_SENTINEL):
        assert secret not in caplog.text and secret not in response.text


async def test_a_failed_test_does_not_count_against_the_destination(web: Api) -> None:
    ident = await destination(web)
    headers = await owner(web)
    with use_registry(registry(Recorder(n.SendResult(False, "timeout", True)))):
        for _ in range(4):
            assert (await post_test(web, headers, ident)).json()["ok"] is False
    async with web.engine.connect() as conn:
        state = (
            await conn.execute(text("SELECT state, consecutive_failures FROM alert_destinations"))
        ).one()
        audit = (
            (
                await conn.execute(
                    text("SELECT details FROM audit_log WHERE action = 'alert.destination.test'")
                )
            )
            .scalars()
            .all()
        )
    assert tuple(state) == ("active", 0)
    assert audit == [{"type": "webhook", "ok": False, "error_code": "timeout"}] * 4


async def test_a_target_that_resolves_inward_is_refused_by_the_test_too(web: Api) -> None:
    async with TlsReceiver() as receiver:
        ident = await destination(web, url=receiver.url())

        async def inward(host: str, port: int) -> list[str]:
            return ["169.254.169.254"]

        settings = make_settings("postgresql+asyncpg://x:y@localhost/z", web_origin=WEB_ORIGIN)
        with use_registry(n.build_registry(settings, receiver.sender(resolver=inward))):
            response = await post_test(web, await owner(web), ident)
    assert response.json() == {"ok": False, "error_code": o.BLOCKED_ADDRESS}
    assert receiver.connections == 0 and PUBLIC_IP


async def test_an_unreadable_secret_reports_config_unreadable(web: Api) -> None:
    ident = await destination(web)
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE alert_destinations SET config_encrypted = :b"), {"b": b"\x01" * 60}
        )
    response = await post_test(web, await owner(web), ident)
    assert response.json() == {"ok": False, "error_code": "config_unreadable"}


@pytest.mark.parametrize("role", ["DEVELOPER", "VIEWER", "SECURITY", "BILLING"])
async def test_only_owners_and_admins_may_send_a_test(web: Api, role: str) -> None:
    ident = await destination(web)
    recorder = Recorder(n.SendResult(True))
    with use_registry(registry(recorder)):
        headers = await person(web, f"{role.lower()}@acme.test", role)
        response = await post_test(web, headers, ident)
        assert response.status_code == 403
        admin = await person(web, "admin@acme.test", "ADMIN")
        assert (await post_test(web, admin, ident)).status_code == 200
    assert len(recorder.calls) == 1  # the refused roles sent nothing


async def test_an_api_key_may_not_send_a_test(web: Api) -> None:
    ident = await destination(web)
    response = await web.client.post(
        f"/v1/alerts/destinations/{ident}/test", headers=web.headers("wide")
    )
    assert response.status_code == 403


async def test_foreign_and_unknown_ids_are_the_same_404(web: Api) -> None:
    from abb_api.ids import public_id

    headers = await owner(web)
    theirs = await seed_destination(web.engine, web.other.context, name="theirs")
    recorder = Recorder(n.SendResult(True))
    seen = []
    with use_registry(registry(recorder)):
        for ident in (
            public_id(IdKind.DESTINATION, theirs),
            new_id(IdKind.DESTINATION),
            "dst_not-an-id",
        ):
            response = await post_test(web, headers, ident)
            body = error(response, 404, "ALERT_DESTINATION_NOT_FOUND")
            seen.append(body["message"])
    assert len(set(seen)) == 1 and recorder.calls == []  # nothing was sent for a foreign id


async def test_tests_are_rate_limited_per_workspace(limited: Api) -> None:
    ident = await destination(limited)
    headers = await owner(limited)
    with use_registry(registry(Recorder(n.SendResult(True)))):
        statuses = [(await post_test(limited, headers, ident)).status_code for _ in range(4)]
    assert statuses == [200, 200, 429, 429]


async def test_a_paused_destination_can_still_be_tested_before_it_is_resumed(web: Api) -> None:
    ident = await destination(web, state="paused")
    with use_registry(registry(Recorder(n.SendResult(True)))):
        response = await post_test(web, await owner(web), ident)
    assert response.json()["ok"] is True
