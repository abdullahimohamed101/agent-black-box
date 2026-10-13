"""The decision endpoint: allow, deny, require_approval, idempotency and what it writes."""

import asyncio

from abb_event_schema.ids import IdKind, new_id
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import Api, build_api
from tests.approvals.helpers import ask, decide, events_of, intent, publish
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event


async def test_without_a_policy_everything_is_allowed_with_version_zero(api: Api) -> None:
    body = intent()
    response = await decide(api, body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["decision"] == "allow" and out["policy_version"] == 0
    assert out["policy_id"] is None and out["approval_id"] is None
    assert out["on_error"] == "closed"  # R3 fails closed by default
    events = await events_of(api, body["run_id"], "policy.")
    assert [e.event_type for e in events] == ["policy.decision"]
    assert events[0].sdk["name"] == "abb-policy-service"
    assert events[0].attributes["policy.decision"] == "allow"


async def test_a_deny_rule_blocks_and_writes_the_blocked_event(api: Api) -> None:
    await publish(api)
    body = intent(capability="filesystem.read", resource="src/.env", risk_class=None, arguments={})
    out = (await decide(api, body)).json()
    assert out["decision"] == "deny" and out["policy_id"] == "deny-env-read"
    assert out["policy_version"] == 1 and out["reason"] == "Do not read dotenv files."
    assert out["approval_id"] is None and out["on_error"] == "open"  # unclassified
    events = await events_of(api, body["run_id"], "policy.")
    assert [e.event_type for e in events] == ["policy.action.blocked"]
    assert events[0].attributes["policy.id"] == "deny-env-read"
    assert events[0].attributes["policy.reason"] == "Do not read dotenv files."


async def test_a_gated_action_creates_one_approval_and_two_events(api: Api) -> None:
    await publish(api)
    body = intent()
    out = (await decide(api, body)).json()
    assert out["decision"] == "require_approval" and out["policy_id"] == "gate-push"
    assert out["approval_state"] == "REQUESTED" and out["on_expiry"] == "deny"
    assert out["approval_id"].startswith("apr_") and out["expires_at"]
    events = await events_of(api, body["run_id"])
    # The decision comes first, then the request it led to (they would tie otherwise).
    assert [e.event_type for e in events] == ["policy.decision", "approval.requested"]
    requested = next(e for e in events if e.event_type == "approval.requested")
    assert requested.attributes["approval.id"] == out["approval_id"]
    assert requested.attributes["approval.state"] == "REQUESTED"
    assert all(e.sdk["name"] == "abb-policy-service" for e in events)


async def test_asking_again_returns_the_same_approval_and_adds_nothing(api: Api) -> None:
    await publish(api)
    body = intent()
    first = (await decide(api, body)).json()
    again = (await decide(api, body)).json()
    assert (
        again["approval_id"] == first["approval_id"] and again["expires_at"] == first["expires_at"]
    )
    assert len(await events_of(api, body["run_id"])) == 2  # nothing new
    other_run = intent()  # a different run is a different intent
    assert (await decide(api, other_run)).json()["approval_id"] != first["approval_id"]
    changed = {**body, "arguments": {"remote": "origin", "force": True}}
    assert (await decide(api, changed)).json()["approval_id"] != first["approval_id"]


async def test_the_rules_fail_mode_beats_the_risk_class_default(api: Api) -> None:
    await publish(api)
    out = (
        await decide(api, intent(capability="deploy.run", resource="prod", risk_class="R1"))
    ).json()
    assert out["on_error"] == "closed" and out["on_expiry"] == "allow"


async def test_an_idempotency_key_makes_allow_events_idempotent(api: Api) -> None:
    run_id = new_id(IdKind.RUN)
    body = intent(run_id, idempotency_key="try-1")
    await decide(api, body)
    await decide(api, body)
    assert len(await events_of(api, run_id, "policy.")) == 1
    await decide(api, intent(run_id, idempotency_key="try-2"))
    assert len(await events_of(api, run_id, "policy.")) == 2


async def test_only_a_project_key_with_the_scope_may_ask(api: Api) -> None:
    body = intent()
    assert (await decide(api, body, "wide")).status_code == 403  # workspace-wide: no project
    assert (await decide(api, body, "reader")).status_code == 403  # runs:read only
    assert (await decide(api, body, "none")).status_code == 401
    assert (await decide(api, body, "writer")).status_code == 200
    assert (await decide(api, body, "ingest_only")).status_code == 200  # events:write is enough


async def test_a_run_of_another_project_is_the_same_404(api: Api) -> None:
    body = intent()
    assert (await decide(api, body, "writer")).status_code == 200
    stolen = await decide(api, body, "beta")  # beta's key, alpha's run
    assert stolen.status_code == 404 and stolen.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_the_body_is_validated_and_bounded(api: Api) -> None:
    for bad in (
        {**intent(), "surprise": 1},
        {**intent(), "capability": "Not A Capability"},
        {**intent(), "risk_class": "R9"},
        {**intent(), "run_id": "run_nope"},
        {**intent(), "arguments": ["a"]},
    ):
        assert (await decide(api, bad)).status_code == 422, bad
    for arguments, reason in (
        ({"t": 0.5}, "float_not_allowed"),
        ({"n": 2**60}, "integer_out_of_range"),
        ({"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}, "too_deep"),
        ({"s": "x" * 5000}, "string_too_long"),
    ):
        response = await decide(api, intent(arguments=arguments))
        assert response.status_code == 422, arguments
        assert response.json()["error"]["details"] == {"reason": reason}
    huge = intent(arguments={"blob": "y" * 200_000})
    assert (await decide(api, huge)).status_code == 413
    response = await api.client.post(
        "/v1/policy/decide",
        content=b"{}",
        headers=api.headers("writer", **{"content-type": "text/plain"}),
    )
    assert response.status_code == 415


async def test_fifty_concurrent_identical_asks_make_one_approval(api: Api) -> None:
    await publish(api)
    body = intent()
    results = await asyncio.gather(*(decide(api, body) for _ in range(50)))
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["approval_id"] for r in results}) == 1
    assert len(await events_of(api, body["run_id"], "approval.requested")) == 1


async def test_a_hostile_nesting_depth_is_refused_without_an_error(api: Api) -> None:
    deep = (
        b'{"run_id": "' + new_id(IdKind.RUN).encode() + b'", "agent_id": "a", "capability": "x.y", '
    )
    deep += b'"arguments": {"a": ' + b"[" * 5000 + b"]" * 5000 + b"}}"
    response = await api.client.post(
        "/v1/policy/decide", content=deep, headers=api.headers("writer")
    )
    assert response.status_code == 422, response.text
    assert "[[[[" not in response.text


async def test_clients_cannot_use_the_servers_emitter_name(api: Api) -> None:
    ids = make_run_ids()
    forged = wire_event(
        ids, 1, event_type="policy.decision", attributes={"policy.decision": "allow"},
        sdk={"name": "abb-policy-service", "version": "1"},
    )  # fmt: skip
    honest = wire_event(ids, 2, event_type="tool.call.completed", attributes={"tool.name": "t"})
    response = (await api.post_batch([forged, honest])).json()
    assert response["accepted"] == 1 and response["rejected"] == 1
    assert response["errors"][0]["issues"][0]["code"] == "emitter_reserved"
    assert await events_of(api, ids["run_id"], "policy.") == []


async def test_open_requests_are_capped_per_key(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url, web_origin="http://localhost:3000", approval_max_open_per_key=3
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        for i in range(3):
            await ask(api, arguments={"i": i})
        refused = await decide(api, intent(arguments={"i": 99}))
        assert refused.status_code == 429 and refused.headers["retry-after"] == "30"
        assert refused.json()["error"]["code"] == "TOO_MANY_OPEN_APPROVALS"
        # The same intent as an open request is still answered (it creates nothing).
        again = await decide(api, intent(run_id=None))  # a new run: a different intent
        assert again.status_code == 429
        # Another key has its own allowance; withdrawing one frees a slot.
        assert (await decide(api, intent(arguments={"i": 1}), "ingest_only")).status_code == 200
