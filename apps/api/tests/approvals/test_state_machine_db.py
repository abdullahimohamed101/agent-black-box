"""The approval state machine is enforced by the database, not only by the service (gate 5).

Each case connects as the application's runtime role and writes SQL by hand, the way a bug or a
stolen credential would. The service's own statements are the only way through; everything else
fails with a check violation (the guard trigger and the CHECK constraints of migration 0093), and
the role cannot delete or truncate. The trigger guards integrity, not authorization: a runtime
credential can still make the legal transitions, which is why those are also audited.
"""

# SQL statements read better on one line each; the f-string builds a column name from fixed lists.
# ruff: noqa: E501, S608

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.ids import parse_public_id
from tests.api_fixtures import NOW, Api
from tests.approvals.helpers import approved, ask, consume, publish
from tests.conftest import runtime_url


@pytest.fixture
async def runtime(database_url: str, runtime_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    yield engine
    await engine.dispose()


async def run_sql(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def refused(engine: AsyncEngine, sql: str, **params: Any) -> None:
    with pytest.raises(IntegrityError, match=r"approval|check constraint"):
        await run_sql(engine, sql, **params)


def uid(approval_id: str) -> uuid.UUID:
    parsed = parse_public_id(IdKind.APPROVAL, approval_id)
    assert parsed is not None
    return parsed


async def a_user(api: Api) -> uuid.UUID:
    async with api.engine.connect() as conn:
        found = (await conn.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
    if found:
        return uuid.UUID(str(found))
    from tests.auth_helpers import add_member

    return (
        await add_member(api.engine, api.tenant.context.workspace_id, "x@acme.test", "ADMIN")
    ).id


async def test_the_requested_intent_cannot_be_changed_in_any_state(
    api: Api, runtime: AsyncEngine
) -> None:
    await publish(api)
    _body, approval_id = await approved(api)
    _waiting_body, waiting = await ask(api, resource="main", arguments={"a": 1})
    for target in (approval_id, waiting):
        for column, value in (
            ("intent_hash", "0" * 64),
            ("capability", "shell.execute"),
            ("resource_scrubbed", "develop"),
            ("arguments_scrubbed", '{"a": 2}'),
            ("risk_class", "R0"),
            ("agent_id", "someone-else"),
            ("run_id", str(uuid.uuid4())),
            ("policy_rule_id", "other-rule"),
            ("policy_version", 99),
            ("approver_role", "DEVELOPER"),
            ("allow_self_approval", True),
            ("on_expiry", "allow"),
            ("requested_by_key_id", "abb_x"),
            ("expires_at", NOW + timedelta(days=30)),
        ):
            cast = {"arguments_scrubbed": "CAST(:v AS jsonb)", "run_id": "CAST(:v AS uuid)"}.get(
                column, ":v"
            )
            await refused(
                runtime, f"UPDATE approvals SET {column} = {cast} WHERE id = :i",
                v=value, i=uid(target),
            )  # fmt: skip


async def test_terminal_states_are_frozen_and_consumption_cannot_be_undone(
    api: Api, runtime: AsyncEngine
) -> None:
    await publish(api)
    body, spent = await approved(api)
    assert (await consume(api, spent, body)).status_code == 200
    await refused(
        runtime,
        "UPDATE approvals SET consumed_at = NULL, consumed_run_id = NULL WHERE id = :i",
        i=uid(spent),
    )
    await refused(runtime, "UPDATE approvals SET consumed_at = now() WHERE id = :i", i=uid(spent))
    await refused(
        runtime,
        "UPDATE approvals SET state = 'EXPIRED', closed_at = now() WHERE id = :i",
        i=uid(spent),
    )
    await refused(runtime, "UPDATE approvals SET state = 'DENIED' WHERE id = :i", i=uid(spent))

    _, denied = await ask(api, arguments={"n": 1})
    user = await a_user(api)
    await run_sql(
        runtime,
        "UPDATE approvals SET state = 'DENIED', decided_by_user_id = :u, decided_at = :t WHERE id = :i",
        u=user, t=NOW, i=uid(denied),
    )  # fmt: skip
    for sql in (
        "UPDATE approvals SET state = 'APPROVED' WHERE id = :i",
        "UPDATE approvals SET state = 'REQUESTED', decided_at = NULL, decided_by_user_id = NULL WHERE id = :i",
        "UPDATE approvals SET decision_comment = 'edited' WHERE id = :i",
        "UPDATE approvals SET state = 'CANCELLED', closed_at = now() WHERE id = :i",
    ):
        await refused(runtime, sql, i=uid(denied))


async def test_only_the_legal_transitions_exist(api: Api, runtime: AsyncEngine) -> None:
    await publish(api)
    user = await a_user(api)
    _, waiting = await ask(api, arguments={"n": 2})
    target = uid(waiting)
    # Not a transition at all; consumed while still waiting; decided without a decider.
    await refused(runtime, "UPDATE approvals SET decision_comment = 'x' WHERE id = :i", i=target)
    await refused(
        runtime,
        "UPDATE approvals SET consumed_at = :t, consumed_run_id = run_id WHERE id = :i",
        i=target,
        t=NOW,
    )
    await refused(
        runtime,
        "UPDATE approvals SET state = 'APPROVED', decided_at = :t WHERE id = :i",
        i=target,
        t=NOW,
    )
    # Decided after it expired, expired before its time, closed without a time.
    await refused(
        runtime,
        "UPDATE approvals SET state = 'APPROVED', decided_at = expires_at + interval '1 second', decided_by_user_id = :u WHERE id = :i",
        i=target,
        u=user,
    )
    await refused(
        runtime,
        "UPDATE approvals SET state = 'EXPIRED', closed_at = expires_at - interval '1 second' WHERE id = :i",
        i=target,
    )
    await refused(runtime, "UPDATE approvals SET state = 'CANCELLED' WHERE id = :i", i=target)
    await refused(runtime, "UPDATE approvals SET state = 'NONSENSE' WHERE id = :i", i=target)
    # The legal ones work, once.
    await run_sql(
        runtime,
        "UPDATE approvals SET state = 'APPROVED', decided_at = requested_at, decided_by_user_id = :u WHERE id = :i",
        u=user, i=target,
    )  # fmt: skip
    # Approved and unspent: may be spent (before expiry) or expire (after), nothing else.
    await refused(
        runtime,
        "UPDATE approvals SET consumed_at = expires_at + interval '1 second', consumed_run_id = run_id WHERE id = :i",
        i=target,
    )
    await refused(
        runtime,
        "UPDATE approvals SET state = 'EXPIRED', closed_at = requested_at WHERE id = :i",
        i=target,
    )
    await refused(
        runtime,
        "UPDATE approvals SET state = 'DENIED', decided_at = requested_at, decided_by_user_id = :u WHERE id = :i",
        i=target,
        u=user,
    )
    await run_sql(
        runtime,
        "UPDATE approvals SET consumed_at = requested_at, consumed_run_id = run_id WHERE id = :i",
        i=target,
    )
    await refused(
        runtime,
        "UPDATE approvals SET consumed_at = requested_at + interval '1 second' WHERE id = :i",
        i=target,
    )


async def test_an_approved_unused_approval_may_expire_and_then_is_frozen(
    api: Api, runtime: AsyncEngine
) -> None:
    await publish(api)
    _body, approval_id = await approved(api)
    target = uid(approval_id)
    await run_sql(
        runtime,
        "UPDATE approvals SET state = 'EXPIRED', closed_at = expires_at WHERE id = :i",
        i=target,
    )
    await refused(
        runtime,
        "UPDATE approvals SET consumed_at = requested_at, consumed_run_id = run_id WHERE id = :i",
        i=target,
    )
    await refused(
        runtime, "UPDATE approvals SET state = 'APPROVED', closed_at = NULL WHERE id = :i", i=target
    )


async def test_a_request_cannot_be_born_decided_or_spent(api: Api, runtime: AsyncEngine) -> None:
    await publish(api)
    _, waiting = await ask(api, arguments={"n": 3})
    for state in ("APPROVED", "DENIED", "EXPIRED"):
        with pytest.raises(IntegrityError, match=r"approval|check constraint"):
            await run_sql(
                runtime,
                "INSERT INTO approvals (workspace_id, id, project_id, run_id, agent_id, capability, "
                "intent_hash, policy_rule_id, policy_version, allow_self_approval, on_expiry, "
                "requested_at, expires_at, requested_by_key_id, state, decided_at, decided_by_user_id, closed_at) "
                "SELECT workspace_id, gen_random_uuid(), project_id, run_id, agent_id, capability, "
                "intent_hash, policy_rule_id, policy_version, allow_self_approval, on_expiry, "
                "requested_at, expires_at, requested_by_key_id || 'x', :s, requested_at, "
                "(SELECT id FROM users LIMIT 1), requested_at FROM approvals WHERE id = :i",
                s=state, i=uid(waiting),
            )  # fmt: skip


async def test_the_runtime_role_cannot_delete_or_truncate_approvals(
    api: Api, runtime: AsyncEngine
) -> None:
    await publish(api)
    _, waiting = await ask(api)
    for sql in (
        "DELETE FROM approvals",
        "TRUNCATE approvals",
        "DELETE FROM approvals WHERE id = :i",
    ):
        with pytest.raises(ProgrammingError, match=r"permission denied"):
            await run_sql(runtime, sql, i=uid(waiting))
    async with api.engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM approvals"))).scalar_one() == 1


async def test_even_the_shape_checks_hold_for_the_owner_role(api: Api) -> None:
    """The CHECK constraints (not the trigger) bind every role, including the schema owner."""
    await publish(api)
    _, waiting = await ask(api)
    with pytest.raises(IntegrityError):
        await run_sql(
            api.engine, "UPDATE approvals SET consumed_at = now() WHERE id = :i", i=uid(waiting)
        )
    with pytest.raises(IntegrityError):
        await run_sql(
            api.engine, "UPDATE approvals SET state = 'DENIED' WHERE id = :i", i=uid(waiting)
        )
