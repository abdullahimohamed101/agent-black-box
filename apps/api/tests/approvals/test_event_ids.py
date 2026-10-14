"""P1-1 (Phase 14 review): the ids of the server's events cannot be pre-claimed by an agent.

The first version derived those ids from public inputs only, so an agent could ingest innocuous
events at the ids the server would use for `approval.granted` and `approval.consumed`; the server's
insert became a swallowed conflict and the run showed no record of the decision. The ids are now an
HMAC under a server-only secret, and a conflict is never swallowed (ADR-141 amendment 2).
"""

import hashlib
import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy import text

from abb_api.approvals.events import derived_event_id
from abb_api.approvals.serverkey import event_id_key
from abb_api.ids import parse_public_id, public_id
from tests.api_fixtures import Api
from tests.approvals.helpers import (
    approval_row,
    approve,
    ask,
    consume,
    decide,
    events_of,
    intent,
    publish,
)
from tests.ingest_helpers import make_run_ids, wire_event


def public_derivation(workspace_id: uuid.UUID, *parts: str) -> str:
    """The first version's derivation: SHA-256 over public inputs (what an attacker computes)."""
    material = b"\0".join(p.encode("utf-8") for p in parts)
    digest = hashlib.sha256(b"abb.server-event.v1\0" + workspace_id.bytes + b"\0" + material)
    return public_id(IdKind.EVENT, uuid.UUID(bytes=digest.digest()[:16]))


def innocuous(ids: dict[str, str], n: int, event_id: str) -> dict:
    return wire_event(
        ids, n, event_id=event_id, event_type="tool.call.completed", attributes={"tool.name": "t"}
    )


async def test_pre_claiming_the_formerly_derivable_ids_does_not_erase_the_record(api: Api) -> None:
    await publish(api)
    ids = make_run_ids()
    body, approval_id = await ask(api, ids["run_id"], trace_id=ids["trace_id"])
    workspace = api.tenant.context.workspace_id
    claimed = [
        public_derivation(workspace, "approval", approval_id, name)
        for name in ("APPROVED", "consumed", "DENIED", "EXPIRED", "CANCELLED")
    ]
    response = await api.post_batch([innocuous(ids, n + 1, e) for n, e in enumerate(claimed)])
    assert response.status_code == 202 and response.json()["accepted"] == 5
    assert (await approve(api, approval_id)).status_code == 200
    assert (await consume(api, approval_id, body)).status_code == 200
    kinds = [e.event_type for e in await events_of(api, ids["run_id"], "approval.")]
    assert "approval.granted" in kinds and "approval.consumed" in kinds


async def test_an_allow_decision_with_an_idempotency_key_cannot_be_pre_claimed(api: Api) -> None:
    await publish(api)
    ids = make_run_ids()
    key = "client-chosen-1"
    workspace = api.tenant.context.workspace_id
    guess = public_derivation(workspace, "decide", ids["run_id"], key)
    assert (await api.post_batch([innocuous(ids, 1, guess)])).status_code == 202
    allowed = intent(ids["run_id"], capability="docs.read", resource="x", idempotency_key=key)
    assert (await decide(api, {**allowed, "trace_id": ids["trace_id"]})).status_code == 200
    kinds = [e.event_type for e in await events_of(api, ids["run_id"], "policy.")]
    assert kinds == ["policy.decision"]


async def test_the_same_idempotency_key_for_another_intent_is_its_own_event(api: Api) -> None:
    await publish(api)
    ids = make_run_ids()
    one = intent(ids["run_id"], capability="docs.read", resource="a", idempotency_key="k1")
    two = intent(ids["run_id"], capability="docs.read", resource="b", idempotency_key="k1")
    for body in (one, two, one):  # the last is a retry of the first: no new event, no error
        assert (await decide(api, {**body, "trace_id": ids["trace_id"]})).status_code == 200
    assert len(await events_of(api, ids["run_id"], "policy.decision")) == 2


async def test_a_client_event_at_a_server_derived_id_is_its_own_and_the_server_fails_loudly(
    api: Api,
) -> None:
    """Only with the secret can an id be computed. If one is occupied anyway (a bug, or a leaked
    key), the transition is refused as a whole: no decision without its record."""
    await publish(api)
    ids = make_run_ids()
    _, approval_id = await ask(api, ids["run_id"], trace_id=ids["trace_id"])
    workspace = api.tenant.context.workspace_id
    key = await event_id_key(api.engine)
    taken = derived_event_id(key, workspace, "approval", approval_id, "APPROVED")
    assert (await api.post_batch([innocuous(ids, 1, taken)])).json()["accepted"] == 1
    response = await approve(api, approval_id)
    assert response.status_code == 500 and response.json()["error"]["code"] == "EVENT_RECORD_FAILED"
    assert (await approval_row(api, approval_id))["state"] == "REQUESTED"  # rolled back together
    assert [e.event_type for e in await events_of(api, ids["run_id"], "approval.granted")] == []


async def test_a_retry_of_the_servers_own_write_is_accepted(api: Api) -> None:
    from abb_api.approvals.events import RunRef, transition_event
    from abb_api.approvals.lifecycle import emit
    from abb_api.approvals.model import Approval  # noqa: F401  (documentation of the type)

    await publish(api)
    ids = make_run_ids()
    _, approval_id = await ask(api, ids["run_id"], trace_id=ids["trace_id"])
    row = await approval_row(api, approval_id)
    key = await event_id_key(api.engine)
    from abb_api.approvals.store import PgApprovalStore

    async with api.engine.begin() as conn:
        approval = await PgApprovalStore(conn, api.tenant.context).get(row["id"])
        assert approval is not None
        ref = RunRef(approval.project_id, approval.run_id, approval.run_id, approval.agent_id)
        first = transition_event(api.tenant.context, ref, approval, now=api.clock(), key=key)
        later = api.clock() + __import__("datetime").timedelta(seconds=5)
        retry = transition_event(api.tenant.context, ref, approval, now=later, key=key)
        assert first.event_id == retry.event_id and first.occurred_at != retry.occurred_at
        await emit(conn, api.tenant.context, [first], __import__("datetime").timedelta(0))
        await emit(conn, api.tenant.context, [retry], __import__("datetime").timedelta(0))


async def test_the_key_is_created_once_stored_and_stable(api: Api) -> None:
    from abb_api.approvals import serverkey

    first = await event_id_key(api.engine)
    serverkey._cache.clear()
    assert await event_id_key(api.engine) == first
    async with api.engine.connect() as conn:
        rows = (await conn.execute(text("SELECT count(*) FROM server_secrets"))).scalar_one()
    assert rows == 1 and len(first) == 32
    assert parse_public_id(IdKind.EVENT, derived_event_id(first, uuid.uuid4(), "x")) is not None
