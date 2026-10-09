"""Sensitive content needs `payload.read` (D10): metadata for readers, bodies for few."""

import pytest

from tests.api_fixtures import Api, person
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_runs_api import error

SECRET = {"prompt": "PAYLOAD-SECRET-9d1", "n": [1, 2]}


async def seeded(api: Api) -> tuple[str, str]:
    run = make_run_ids()
    event = wire_event(run, 1, payload=SECRET)
    assert (await api.post_batch([event])).status_code == 202
    await api.drain()
    return run["run_id"], event["event_id"]


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "DEVELOPER", "SECURITY"])
async def test_roles_with_payload_read_get_the_body(web: Api, role: str) -> None:
    run_id, event_id = await seeded(web)
    headers = await person(web, f"{role.lower()}@acme.test", role)
    detail = (await web.client.get(f"/v1/runs/{run_id}/events/{event_id}", headers=headers)).json()
    assert detail["payload"] == SECRET and detail["payload_withheld"] is False


async def test_a_viewer_gets_metadata_and_is_told_the_payload_is_withheld(web: Api) -> None:
    run_id, event_id = await seeded(web)
    headers = await person(web, "viewer@acme.test", "VIEWER")
    response = await web.client.get(f"/v1/runs/{run_id}/events/{event_id}", headers=headers)
    assert response.status_code == 200
    assert "PAYLOAD-SECRET" not in response.text
    detail = response.json()
    assert detail["payload"] is None and detail["payload_withheld"] is True
    assert detail["has_payload"] is True  # the fact that one exists is metadata
    assert detail["event_type"] and detail["attributes"] is not None


async def test_lists_never_carry_a_payload_for_anyone(web: Api) -> None:
    run_id, _ = await seeded(web)
    for role in ("OWNER", "VIEWER"):
        headers = await person(web, f"{role.lower()}@acme.test", role)
        listed = await web.client.get(f"/v1/runs/{run_id}/events", headers=headers)
        assert "PAYLOAD-SECRET" not in listed.text
        assert all(
            i["payload"] is None and i["payload_withheld"] is False for i in listed.json()["items"]
        )


async def test_a_key_with_runs_read_keeps_its_payloads(web: Api) -> None:
    run_id, event_id = await seeded(web)
    detail = (await web.get(f"/v1/runs/{run_id}/events/{event_id}", token="reader")).json()
    assert detail["payload"] == SECRET and detail["payload_withheld"] is False


async def test_billing_cannot_reach_events_at_all_and_unknown_events_stay_404_for_viewers(
    web: Api,
) -> None:
    run_id, event_id = await seeded(web)
    billing = await person(web, "billing@acme.test", "BILLING")
    error(
        await web.client.get(f"/v1/runs/{run_id}/events/{event_id}", headers=billing),
        403, "PERMISSION_DENIED",
    )  # fmt: skip
    viewer = await person(web, "viewer@acme.test", "VIEWER")
    missing = await web.client.get(
        f"/v1/runs/{run_id}/events/evt_01HZZZZZZZZZZZZZZZZZZZZZZZ", headers=viewer
    )
    assert missing.status_code == 404


async def test_artifact_content_is_forbidden_to_a_viewer_whatever_the_id(web: Api) -> None:
    viewer = await person(web, "viewer@acme.test", "VIEWER")
    for artifact_id in ("art_01HZZZZZZZZZZZZZZZZZZZZZZZ", "art_garbage", "x"):
        response = await web.client.get(f"/v1/artifacts/{artifact_id}/content", headers=viewer)
        error(response, 403, "PERMISSION_DENIED")
