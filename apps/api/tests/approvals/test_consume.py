"""Replay: an approval authorizes one intent, once (spec §90, gates 2 and 5).

Every row of the threat model's "approval replay" is attacked directly: another command, resource,
argument, agent, run, risk class, a second use, use after expiry, use before approval, a denied or
cancelled approval, and another workspace, project or key.
"""

import asyncio
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id

from tests.api_fixtures import Api
from tests.approvals.helpers import (
    approval_row,
    approved,
    ask,
    consume,
    decision,
    events_of,
    later,
    member,
    publish,
)
from tests.test_audit import rows

MISMATCH = "APPROVAL_INTENT_MISMATCH"


async def code(response: Any) -> str:
    assert response.status_code in (404, 409), response.text
    return str(response.json()["error"]["code"])


async def test_the_exact_intent_is_accepted_once(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    first = await consume(api, approval_id, body)
    assert first.status_code == 200, first.text
    assert first.json()["approval_id"] == approval_id
    second = await consume(api, approval_id, body)
    assert second.status_code == 409 and await code(second) == "APPROVAL_CONSUMED"
    row = await approval_row(api, approval_id)
    assert row["consumed_at"] is not None and row["state"] == "APPROVED"
    consumed = await events_of(api, body["run_id"], "approval.consumed")
    assert len(consumed) == 1 and consumed[0].attributes["approval.id"] == approval_id


@pytest.mark.parametrize(
    "change",
    [
        {"resource": "develop"},
        {"arguments": {"remote": "origin", "force": True}},
        {"arguments": {"remote": "origin"}},
        {"agent_id": "other-agent"},
        {"risk_class": "R4"},
        {"risk_class": None},
        {"capability": "github.merge"},
        {"run_id": new_id(IdKind.RUN)},
        {"arguments": {"remote": "origin", "force": False, "extra": 0}},
    ],
    ids=lambda c: next(iter(c)),
)
async def test_any_other_intent_is_refused_and_spends_nothing(
    api: Api, change: dict[str, Any]
) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    refused = await consume(api, approval_id, {**body, **change})
    assert refused.status_code == 409 and await code(refused) == MISMATCH
    assert (await approval_row(api, approval_id))["consumed_at"] is None
    assert (await consume(api, approval_id, body)).status_code == 200  # the real one still works


async def test_text_that_differs_only_in_normalisation_is_the_same_intent(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api, arguments={"path": "café"})
    same = {**body, "arguments": {"path": "café"}}
    assert (await consume(api, approval_id, same)).status_code == 200


async def test_the_refusal_never_echoes_the_intent(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    secret = "ECHO-CANARY-77"
    response = await consume(api, approval_id, {**body, "resource": secret})
    assert secret not in response.text and body["resource"] not in response.text
    assert set(response.json()["error"]["details"]) <= {"state"}


async def test_before_approval_denied_and_cancelled_are_not_approved(api: Api) -> None:
    await publish(api)
    body, waiting = await ask(api)
    assert await code(await consume(api, waiting, body)) == "APPROVAL_NOT_APPROVED"
    headers = await member(api, "admin@acme.test", "ADMIN")
    assert (await decision(api, headers, waiting, "deny")).status_code == 200
    assert await code(await consume(api, waiting, body)) == "APPROVAL_NOT_APPROVED"
    body2, withdrawn = await ask(api)
    cancel = await api.client.delete(f"/v1/approvals/{withdrawn}", headers=api.headers("writer"))
    assert cancel.status_code == 204
    assert await code(await consume(api, withdrawn, body2)) == "APPROVAL_NOT_APPROVED"


async def test_after_expiry_an_approved_approval_is_expired_not_usable(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    later(api, minutes=16)  # gate-push expires in 15 minutes
    refused = await consume(api, approval_id, body)
    assert refused.status_code == 409 and await code(refused) == "APPROVAL_EXPIRED"
    row = await approval_row(api, approval_id)
    assert row["state"] == "EXPIRED" and row["consumed_at"] is None
    assert len(await events_of(api, body["run_id"], "approval.expired")) == 1


async def test_a_request_that_expires_unanswered_cannot_be_approved_late(api: Api) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    later(api, minutes=16)
    headers = await member(api, "admin@acme.test", "ADMIN")
    late = await decision(api, headers, approval_id, "approve")
    assert late.status_code == 409 and late.json()["error"]["code"] == "APPROVAL_NOT_REQUESTED"
    assert late.json()["error"]["details"] == {"state": "EXPIRED"}
    assert await code(await consume(api, approval_id, body)) == "APPROVAL_EXPIRED"


async def test_another_key_project_or_workspace_gets_the_same_404(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    unknown = await consume(api, "apr_01J9ZZZZZZZZZZZZZZZZZZZZZZ", body)
    assert unknown.status_code == 404
    for token in (
        "ingest_only",
        "beta",
        "other",
    ):  # same project other key, other project, other ws
        response = await consume(api, approval_id, body, token)
        assert response.status_code == 404, token
        assert response.json()["error"]["code"] == "APPROVAL_NOT_FOUND", token
        assert response.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert (await approval_row(api, approval_id))["consumed_at"] is None


async def test_fifty_simultaneous_consumes_have_exactly_one_winner(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    results = await asyncio.gather(*(consume(api, approval_id, body) for _ in range(50)))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200] + [409] * 49
    assert {r.json()["error"]["code"] for r in results if r.status_code == 409} == {
        "APPROVAL_CONSUMED"
    }
    assert len(await events_of(api, body["run_id"], "approval.consumed")) == 1
    assert len([r for r in await rows(api) if r.action == "approval.consume"]) == 1


async def test_consuming_is_audited_by_the_key_without_the_intent(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    await consume(api, approval_id, body)
    entry = next(r for r in await rows(api) if r.action == "approval.consume")
    assert entry.actor_kind == "api_key" and entry.actor_id.startswith("key:")
    assert entry.details == {
        "approval_id": approval_id,
        "current_policy_rule": "gate-push",
        "current_policy_version": 1,
    }
    assert "main" not in str(entry.details)


async def test_a_workspace_wide_key_cannot_use_approvals(api: Api) -> None:
    await publish(api)
    body, approval_id = await approved(api)
    response = await consume(api, approval_id, body, "wide")
    assert response.status_code == 403
