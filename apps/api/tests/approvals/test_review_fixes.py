"""Phase 14 review fixes on the server: P3-4 (asking cannot create runs), P3-8 (audited replay)."""

from datetime import timedelta

from abb_event_schema.ids import IdKind, new_id

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from tests.api_fixtures import Api
from tests.approvals.helpers import approve, ask, consume, decide, intent, publish
from tests.test_audit import rows


async def ask_only_key(api: Api) -> str:
    async with api.engine.begin() as conn:
        created = await ApiKeyRepository(conn, api.tenant.context).create(
            scopes=frozenset({scopes.POLICY_CHECK}),
            project_id=api.tenant.project_uuids["alpha"],
            created_by=None,
        )
    api.tokens["asker"] = created.token
    return "asker"


async def test_an_ask_only_key_cannot_create_a_run_by_asking(api: Api) -> None:
    await publish(api)
    token = await ask_only_key(api)
    fresh = new_id(IdKind.RUN)
    response = await decide(api, intent(fresh, capability="x.y", resource="z"), token)
    assert response.status_code == 404 and response.json()["error"]["code"] == "RUN_NOT_FOUND"
    # A run another key created is askable: the key may ask, it may not invent runs.
    body, _ = await ask(api)
    again = await decide(api, {**body, "capability": "x.y", "resource": "z"}, token)
    assert again.status_code == 200


async def test_an_ingesting_key_may_still_ask_before_its_first_event(api: Api) -> None:
    await publish(api)
    response = await decide(api, intent(capability="x.y", resource="z"))
    assert response.status_code == 200


async def test_a_replay_attempt_is_audited_once_a_minute(api: Api) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    assert (await approve(api, approval_id)).status_code == 200
    other = {**body, "arguments": {"remote": "origin", "force": True}}
    for _ in range(5):
        assert (await consume(api, approval_id, other)).status_code == 409
    refused = [r for r in await rows(api) if r.action == "approval.consume_refused"]
    assert len(refused) == 1 and refused[0].details["reason"] == "APPROVAL_INTENT_MISMATCH"
    assert "force" not in str(refused[0].details)
    api.clock.now = api.clock.now + timedelta(seconds=61)
    assert (await consume(api, approval_id, other)).status_code == 409
    refused = [r for r in await rows(api) if r.action == "approval.consume_refused"]
    assert len(refused) == 2
