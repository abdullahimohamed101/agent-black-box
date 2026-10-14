"""Deciding an approval as a person (gates 5 and 6): authorization, races, audit, events."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from tests.api_fixtures import Api
from tests.approvals.helpers import (
    SENTINEL,
    approval_row,
    ask,
    consume,
    decide,
    decision,
    events_of,
    intent,
    key_made_by,
    later,
    member,
    publish,
)
from tests.test_audit import rows

ROLES = ("OWNER", "ADMIN", "SECURITY", "DEVELOPER", "VIEWER", "BILLING")


async def as_role(api: Api, role: str) -> dict[str, str]:
    return await member(api, f"{role.lower()}@acme.test", role)


async def test_an_admin_approves_and_it_leaves_one_event_and_one_audit_row(api: Api) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    headers = await as_role(api, "ADMIN")
    response = await decision(api, headers, approval_id, "approve", comment="looks fine")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["state"] == "APPROVED" and detail["comment"] == "looks fine"
    assert detail["decided_by"].startswith("usr_") and "intent_hash" not in response.text
    granted = await events_of(api, body["run_id"], "approval.granted")
    assert len(granted) == 1 and granted[0].attributes["approval.state"] == "APPROVED"
    audit = [r for r in await rows(api) if r.action == "approval.decide"]
    assert len(audit) == 1
    assert audit[0].actor_kind == "user" and audit[0].resource_id == approval_id
    assert audit[0].details == {
        "approval_id": approval_id,
        "decision": "approve",
        "approver_role": "ADMIN",
        "policy_id": "gate-push",
    }


async def test_a_denial_is_recorded_and_final(api: Api) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    headers = await as_role(api, "OWNER")
    assert (await decision(api, headers, approval_id, "deny")).json()["state"] == "DENIED"
    assert len(await events_of(api, body["run_id"], "approval.denied")) == 1
    again = await decision(api, headers, approval_id, "approve")
    assert again.status_code == 409 and again.json()["error"]["code"] == "APPROVAL_NOT_REQUESTED"


@pytest.mark.parametrize(
    ("capability", "allowed"),
    [
        ("github.push", {"OWNER", "ADMIN"}),  # no approver_role: owner or admin
        ("deploy.run", {"OWNER", "ADMIN", "SECURITY"}),  # approver_role: security
        ("billing.change", {"OWNER"}),
        ("lint.fix", {"OWNER", "ADMIN", "SECURITY", "DEVELOPER"}),
    ],
)
async def test_every_role_against_every_approver_role(
    api: Api, capability: str, allowed: set[str]
) -> None:
    await publish(api)
    resource = "main" if capability == "github.push" else "x"
    for role in ROLES:
        _body, approval_id = await ask(api, capability=capability, resource=resource)
        response = await decision(api, await as_role(api, role), approval_id, "approve")
        if role in allowed:
            assert response.status_code == 200, (capability, role, response.text)
        elif role in ("VIEWER", "BILLING"):
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"  # no approval.decide
        else:
            assert response.status_code == 403, (capability, role)
            assert response.json()["error"]["code"] == "APPROVER_ROLE_REQUIRED"
        row = await approval_row(api, approval_id)
        assert row["state"] == ("APPROVED" if role in allowed else "REQUESTED")


async def test_an_api_key_can_never_decide_or_read_approvals(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    for token in ("writer", "reader", "wide", "wide_reader", "ingest_only"):
        for method, path in (
            ("POST", f"/v1/approvals/{approval_id}/decision"),
            ("GET", f"/v1/approvals/{approval_id}"),
            ("GET", "/v1/approvals"),
            ("GET", "/v1/approvals/pending-count"),
        ):
            response = await api.client.request(
                method,
                path,
                json={"decision": "approve"} if method == "POST" else None,
                headers=api.headers(token),
            )
            assert response.status_code == 403, (token, path)
    assert (await approval_row(api, approval_id))["state"] == "REQUESTED"


async def test_a_session_cannot_ask_wait_consume_or_cancel(api: Api) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    headers = await as_role(api, "OWNER")
    calls = (
        api.client.post("/v1/policy/decide", json=body, headers=headers),
        api.client.get(f"/v1/approvals/{approval_id}/wait", headers=headers),
        api.client.post(
            f"/v1/approvals/{approval_id}/consume", json={"intent": body}, headers=headers
        ),
        api.client.delete(f"/v1/approvals/{approval_id}", headers=headers),
    )
    for response in await asyncio.gather(*calls):
        assert response.status_code == 403


async def test_the_requester_cannot_approve_their_own_request_unless_the_rule_allows(
    api: Api,
) -> None:
    await publish(api)
    dana = await member(api, "dana@acme.test", "ADMIN")
    token = await key_made_by(api, "dana@acme.test")
    body = intent()
    approval_id = (await decide(api, body, token)).json()["approval_id"]
    blocked = await decision(api, dana, approval_id, "approve")
    assert (
        blocked.status_code == 403 and blocked.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    )
    assert (await approval_row(api, approval_id))["state"] == "REQUESTED"
    other = await as_role(api, "OWNER")
    assert (await decision(api, other, approval_id, "approve")).status_code == 200

    # A rule with allow_self_approval lets the same person approve their own agent's action.
    own = (await decide(api, intent(capability="notes.write", resource="n"), token)).json()
    assert (await decision(api, dana, own["approval_id"], "approve")).status_code == 200


async def test_a_refused_self_approval_is_audited(api: Api) -> None:
    await publish(api)
    dana = await member(api, "dana@acme.test", "ADMIN")
    token = await key_made_by(api, "dana@acme.test")
    approval_id = (await decide(api, intent(), token)).json()["approval_id"]
    await decision(api, dana, approval_id, "approve")
    denied = [r for r in await rows(api) if r.action == "approval.decide" and r.outcome == "denied"]
    assert len(denied) == 1 and denied[0].actor_kind == "user"


async def test_a_member_removed_after_asking_loses_the_right_to_decide(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    headers = await as_role(api, "ADMIN")
    async with api.engine.begin() as conn:
        await conn.execute(text("DELETE FROM workspace_members WHERE role = 'ADMIN'"))
    response = await decision(api, headers, approval_id, "approve")
    assert response.status_code == 404  # not a member any more: no such workspace
    assert (await approval_row(api, approval_id))["state"] == "REQUESTED"


async def test_a_foreign_or_unknown_approval_is_the_same_404_for_people(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    other = await member(api, "owner@globex.test", "OWNER", "globex")
    foreign = await decision(api, other, approval_id, "approve")
    unknown = await decision(api, other, "apr_01J9ZZZZZZZZZZZZZZZZZZZZZZ", "approve")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
    for path in (f"/v1/approvals/{approval_id}",):
        assert (await api.client.get(path, headers=other)).status_code == 404
    listing = (await api.client.get("/v1/approvals?state=all", headers=other)).json()
    assert listing["items"] == []


@pytest.mark.parametrize(
    "racers", [("approve", "deny"), ("approve", "approve"), ("deny", "cancel")]
)
async def test_concurrent_transitions_have_exactly_one_winner(
    api: Api, racers: tuple[str, str]
) -> None:
    await publish(api)
    body, approval_id = await ask(api)
    owner = await as_role(api, "OWNER")
    admin = await as_role(api, "ADMIN")

    async def run(kind: str, headers: dict[str, str]) -> Any:
        if kind == "cancel":
            return await api.client.delete(
                f"/v1/approvals/{approval_id}", headers=api.headers("writer")
            )
        return await decision(api, headers, approval_id, kind)

    results = await asyncio.gather(
        *(run(k, h) for k, h in zip(racers, (owner, admin), strict=True))
    )
    wins = [r for r in results if r.status_code in (200, 204)]
    losses = [r for r in results if r.status_code == 409]
    assert len(wins) == 1 and len(losses) == 1, [r.status_code for r in results]
    assert losses[0].json()["error"]["code"] == "APPROVAL_NOT_REQUESTED"
    final = (await approval_row(api, approval_id))["state"]
    assert final in ("APPROVED", "DENIED", "CANCELLED")
    transitions = await events_of(api, body["run_id"], "approval.")
    assert [e.event_type for e in transitions if e.event_type != "approval.requested"] == [
        {
            "APPROVED": "approval.granted",
            "DENIED": "approval.denied",
            "CANCELLED": "approval.cancelled",
        }[final]
    ]
    decided = [r for r in await rows(api) if r.action in ("approval.decide", "approval.cancel")]
    assert len(decided) == 1


async def test_a_decision_racing_the_clock_has_one_winner(api: Api) -> None:
    """Approve vs expiry: whichever statement sees the row first wins; never both."""
    await publish(api)
    _body, approval_id = await ask(api)
    owner = await as_role(api, "OWNER")
    later(api, minutes=16)
    results = await asyncio.gather(
        decision(api, owner, approval_id, "approve"),
        api.client.get(f"/v1/approvals/{approval_id}", headers=owner),
    )
    assert results[0].status_code == 409
    row = await approval_row(api, approval_id)
    assert row["state"] == "EXPIRED" and row["decided_by_user_id"] is None


async def test_the_comment_is_scrubbed_and_bounded(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    headers = await as_role(api, "OWNER")
    secret = "ghp_" + "a" * 36
    response = await decision(api, headers, approval_id, "deny", comment=f"token {secret} leaked")
    assert response.status_code == 200 and secret not in response.text
    assert secret not in (await approval_row(api, approval_id))["decision_comment"]
    too_long = await decision(api, headers, approval_id, "deny", comment="x" * 1001)
    assert too_long.status_code == 422


async def test_nothing_sensitive_reaches_the_events_or_the_audit_log(api: Api) -> None:
    await publish(api)
    body = intent(resource=SENTINEL, arguments={"note": SENTINEL, "token": "ghp_" + "b" * 36})
    out = (await decide(api, body)).json()
    approval_id = out["approval_id"]
    await decision(api, await as_role(api, "ADMIN"), approval_id, "approve", comment=SENTINEL)
    await consume(api, approval_id, body)
    texts = [str(e.attributes) for e in await events_of(api, body["run_id"])]
    texts += [str(r.details) for r in await rows(api)]
    texts.append(str(out))
    assert not [t for t in texts if SENTINEL in t or "ghp_" in t]
