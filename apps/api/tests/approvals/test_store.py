"""The approvals store on its own: each transition is one statement with its own predicates, so
the layers behind it (the service checks, the sweep, the trigger) are not the only defence."""

import uuid
from datetime import timedelta

from abb_event_schema.ids import IdKind

from abb_api.approvals.model import Approval, NewApproval
from abb_api.approvals.store import PgApprovalStore
from abb_api.ids import new_uuid
from tests.api_fixtures import NOW, Api
from tests.auth_helpers import add_member

HASH = "a" * 64


async def make(api: Api, store: PgApprovalStore, **over: object) -> Approval:
    fields: dict[str, object] = {
        "id": new_uuid(IdKind.APPROVAL),
        "project_id": api.tenant.project_uuids["alpha"],
        "run_id": uuid.uuid4(),
        "agent_id": "agent",
        "capability": "x.y",
        "resource_scrubbed": "r",
        "risk_class": "R3",
        "arguments_scrubbed": {"a": 1},
        "scrub_dropped": False,
        "scrub_count": 0,
        "intent_hash": HASH,
        "policy_rule_id": "rule",
        "policy_version": 1,
        "approver_role": None,
        "allow_self_approval": False,
        "on_expiry": "deny",
        "requested_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
        "requested_by_key_id": "key-1",
        "requested_by_user_id": None,
    }
    fields.update(over)
    return await store.insert(NewApproval(**fields))  # type: ignore[arg-type]


async def test_each_transition_demands_its_own_state_time_and_owner(api: Api) -> None:
    user = (
        await add_member(api.engine, api.tenant.context.workspace_id, "u@acme.test", "ADMIN")
    ).id
    project = api.tenant.project_uuids["alpha"]
    async with api.engine.begin() as conn:
        store = PgApprovalStore(conn, api.tenant.context)
        before = NOW + timedelta(minutes=1)
        after = NOW + timedelta(minutes=16)

        # decide: only a waiting, unexpired request, once
        late = await make(api, store)
        assert (
            await store.decide(late.id, state="APPROVED", user_id=user, comment=None, now=after)
            is None
        )
        first = await store.decide(
            late.id, state="APPROVED", user_id=user, comment="ok", now=before
        )
        assert first is not None and first.state == "APPROVED" and first.decision_comment == "ok"
        assert (
            await store.decide(late.id, state="DENIED", user_id=user, comment=None, now=before)
            is None
        )

        # consume: approved, unspent, unexpired, this key and project, this hash and run
        approval = first
        for bad in (
            {"key_id": "other-key"},
            {"project_id": uuid.uuid4()},
            {"intent_hash": "b" * 64},
            {"run_id": uuid.uuid4()},
            {"now": after},
        ):
            args = {
                "project_id": project,
                "key_id": "key-1",
                "intent_hash": HASH,
                "run_id": approval.run_id,
                "now": before,
                **bad,
            }
            assert await store.consume(approval.id, **args) is None, bad  # type: ignore[arg-type]
        spent = await store.consume(
            approval.id, project_id=project, key_id="key-1", intent_hash=HASH,
            run_id=approval.run_id, now=before,
        )  # fmt: skip
        assert spent is not None and spent.consumed_at == before
        assert await store.consume(
            approval.id, project_id=project, key_id="key-1", intent_hash=HASH,
            run_id=approval.run_id, now=before,
        ) is None  # fmt: skip

        # cancel: the asking key, in this project, while waiting and unexpired
        waiting = await make(api, store)
        assert (
            await store.cancel(waiting.id, project_id=project, key_id="other", now=before) is None
        )
        assert (
            await store.cancel(waiting.id, project_id=uuid.uuid4(), key_id="key-1", now=before)
            is None
        )
        assert await store.cancel(waiting.id, project_id=project, key_id="key-1", now=after) is None
        cancelled = await store.cancel(waiting.id, project_id=project, key_id="key-1", now=before)
        assert cancelled is not None and cancelled.state == "CANCELLED"

        # expire: only open rows whose time has come
        due = await make(api, store)
        assert await store.expire(due.id, before) is None
        expired = await store.expire(due.id, after)
        assert expired is not None and expired.state == "EXPIRED"
        assert await store.expire(due.id, after) is None


async def test_the_agent_view_is_confined_to_workspace_project_and_key(api: Api) -> None:
    async with api.engine.begin() as conn:
        store = PgApprovalStore(conn, api.tenant.context)
        approval = await make(api, store)
        project = api.tenant.project_uuids["alpha"]
        assert await store.get(approval.id, project_id=project, key_id="key-1") is not None
        assert await store.get(approval.id, project_id=project, key_id="key-2") is None
        assert await store.get(approval.id, project_id=uuid.uuid4(), key_id="key-1") is None
        assert await store.get(approval.id) is not None  # a person's view: the workspace
        foreign = PgApprovalStore(conn, api.other.context)
        assert await foreign.get(approval.id) is None
        assert await foreign.get(approval.id, project_id=project, key_id="key-1") is None
        assert (
            await foreign.listing(
                pending_at=None, decided=False, project_id=None, limit=10, before=None
            )
            == []
        )
        assert await foreign.pending_count(NOW, None) == 0


async def test_the_sweep_is_bounded_oldest_first_and_filterable_by_run(api: Api) -> None:
    async with api.engine.begin() as conn:
        store = PgApprovalStore(conn, api.tenant.context)
        run = uuid.uuid4()
        made = []
        for i in range(4):
            made.append(
                await make(
                    api,
                    store,
                    run_id=run if i == 0 else uuid.uuid4(),
                    requested_by_key_id=f"k{i}",
                    expires_at=NOW + timedelta(minutes=10 + i),
                )
            )
        later = NOW + timedelta(hours=1)
        assert [a.id for a in await store.expire_due(NOW, limit=10)] == []
        only_run = await store.expire_due(later, limit=10, run_id=run)
        assert [a.id for a in only_run] == [made[0].id]
        two = await store.expire_due(later, limit=2)
        assert [a.id for a in two] == [made[1].id, made[2].id]  # oldest first, bounded
        assert [a.id for a in await store.expire_due(later, limit=10)] == [made[3].id]
        assert await store.expire_due(later, limit=10) == []
