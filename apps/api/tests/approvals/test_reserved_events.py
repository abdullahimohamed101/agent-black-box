"""Clients cannot write the events the server writes (ADR-141, KI-143)."""

import pytest

from abb_api.db import tables as t
from abb_api.ingestion.emitter import RESERVED_EVENT_TYPES
from tests.api_fixtures import Api
from tests.approvals.helpers import approved, events_of, publish
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_ingestion_api import count

ATTRIBUTES = {"approval.id": "apr_01J9ZZZZZZZZZZZZZZZZZZZZZZ", "policy.id": "p"}


@pytest.mark.parametrize("event_type", sorted(RESERVED_EVENT_TYPES))
async def test_a_reserved_type_is_rejected_per_event_and_the_rest_is_stored(
    api: Api, event_type: str
) -> None:
    ids = make_run_ids()
    forged = wire_event(ids, 1, event_type=event_type, attributes=dict(ATTRIBUTES))
    honest = wire_event(ids, 2, event_type="tool.call.completed", attributes={"tool.name": "t"})
    response = await api.post_batch([forged, honest])
    assert response.status_code == 202
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["errors"][0]["code"] == "RESERVED_EVENT_TYPE" and body["errors"][0]["index"] == 0
    assert body["errors"][0]["event_id"] == forged["event_id"]
    assert await count(api.engine, t.events) == 1
    assert await events_of(api, ids["run_id"], "policy.") == []
    assert await events_of(api, ids["run_id"], "approval.") == []


async def test_policy_warning_stays_client_writable(api: Api) -> None:
    ids = make_run_ids()
    warning = wire_event(
        ids, 1, event_type="policy.warning", attributes={"policy.id": "p", "policy.reason": "x"}
    )
    body = (await api.post_batch([warning])).json()
    assert body["accepted"] == 1 and body["rejected"] == 0


async def test_the_servers_own_events_still_reach_the_run(api: Api) -> None:
    await publish(api)
    body, _ = await approved(api)
    kinds = [e.event_type for e in await events_of(api, body["run_id"])]
    assert {"policy.decision", "approval.requested", "approval.granted"} <= set(kinds)


async def test_the_single_event_endpoint_refuses_a_reserved_type(api: Api) -> None:
    ids = make_run_ids()
    forged = wire_event(ids, 1, event_type="approval.granted", attributes=dict(ATTRIBUTES))
    response = await api.client.post("/v1/events", json=forged, headers=api.headers("writer"))
    assert response.status_code == 422, response.text
    assert await count(api.engine, t.events) == 0
