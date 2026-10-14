"""A granted approval does not outlive a policy that now denies its action (ADR-141, KI-144)."""

import asyncio

from tests.api_fixtures import Api
from tests.approvals.helpers import (
    POLICY,
    approval_row,
    approved,
    ask,
    consume,
    events_of,
    publish,
)
from tests.test_audit import rows

DENY_PUSH = """\
version: 1
policies:
  - id: freeze-main
    description: Main is frozen.
    when: {capability: github.push, resource: main}
    decision: deny
"""
ALLOW_PUSH = """\
version: 1
policies:
  - id: open-main
    description: Pushing is fine.
    when: {capability: github.push}
    decision: allow
"""


async def code(response: object) -> str:
    return str(response.json()["error"]["code"])  # type: ignore[attr-defined]


async def test_a_deny_published_after_the_grant_refuses_the_consume(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    await publish(api, DENY_PUSH)
    refused = await consume(api, approval_id, body)
    assert refused.status_code == 409 and await code(refused) == "APPROVAL_POLICY_DENIED"
    assert "freeze-main" not in refused.text and body["resource"] not in refused.text

    row = await approval_row(api, approval_id)
    assert row["state"] == "APPROVED" and row["consumed_at"] is None  # unchanged
    assert await events_of(api, body["run_id"], "approval.consumed") == []
    blocked = await events_of(api, body["run_id"], "policy.action.blocked")
    assert len(blocked) == 1
    assert blocked[0].attributes["policy.id"] == "freeze-main"
    assert blocked[0].attributes["policy.version"] == 2
    assert blocked[0].attributes["approval.id"] == approval_id
    assert blocked[0].sdk["name"] == "abb-policy-service"
    audit = [r for r in await rows(api) if r.action == "approval.consume_refused"]
    assert len(audit) == 1 and audit[0].details["policy_version"] == 2

    again = await consume(api, approval_id, body)  # a retry is the same refusal, same event
    assert await code(again) == "APPROVAL_POLICY_DENIED"
    assert len(await events_of(api, body["run_id"], "policy.action.blocked")) == 1


async def test_an_unrelated_intent_is_not_affected_by_the_new_deny(api: Api) -> None:
    await publish(api)
    other, other_id = await approved(api, capability="deploy.run", resource="prod", risk_class="R4")
    await publish(api, DENY_PUSH + POLICY.split("policies:\n", 1)[1])
    spent = await consume(api, other_id, other)
    assert spent.status_code == 200, spent.text


async def test_publishing_an_allow_again_makes_the_approval_usable(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    await publish(api, DENY_PUSH)
    assert await code(await consume(api, approval_id, body)) == "APPROVAL_POLICY_DENIED"
    await publish(api, ALLOW_PUSH)
    spent = await consume(api, approval_id, body)
    assert spent.status_code == 200, spent.text
    consumed = await events_of(api, body["run_id"], "approval.consumed")
    assert consumed[0].attributes["policy.current_id"] == "open-main"
    assert consumed[0].attributes["policy.current_version"] == 3
    assert consumed[0].attributes["policy.version"] == 1  # the version that required it
    audit = [r for r in await rows(api) if r.action == "approval.consume"]
    assert audit[0].details["current_policy_version"] == 3


async def test_a_policy_that_still_requires_approval_lets_the_consume_proceed(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    await publish(api, POLICY.replace("Pushing to main needs a person.", "Needs a person."))
    spent = await consume(api, approval_id, body)
    assert spent.status_code == 200, spent.text
    consumed = await events_of(api, body["run_id"], "approval.consumed")
    assert consumed[0].attributes["policy.current_id"] == "gate-push"
    assert consumed[0].attributes["policy.current_version"] == 2


async def test_a_deny_published_before_the_consume_wins_a_race(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    results = await asyncio.gather(
        publish(api, DENY_PUSH),
        *(consume(api, approval_id, body) for _ in range(8)),
    )
    # Each consume was ordered before or after the publish (they share a lock): those after it
    # are refused, those before it won legitimately, and the approval is spent at most once.
    codes = [r.status_code for r in results[1:]]  # type: ignore[union-attr]
    assert codes.count(200) <= 1
    row = await approval_row(api, approval_id)
    after = await consume(api, approval_id, body)
    if row["consumed_at"] is None:
        assert await code(after) == "APPROVAL_POLICY_DENIED"
    else:
        assert await code(after) == "APPROVAL_CONSUMED"


async def test_a_pending_request_is_unaffected_until_it_is_spent(api: Api) -> None:
    await publish(api)
    _, approval_id = await ask(api)
    await publish(api, DENY_PUSH)
    assert (await approval_row(api, approval_id))["state"] == "REQUESTED"
