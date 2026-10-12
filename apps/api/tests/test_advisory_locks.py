"""Count-then-insert bounds run under advisory locks on documented keys (D10, ADR-090).

Scheduling cannot prove a lock exists, so these tests take the lock themselves in another
transaction and show that the request waits for it, then completes once it is released. Nothing
here takes `RunRepository.lock`: that row lock belongs to the summarizer.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid, to_uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import Api, person
from tests.evaluation_helpers import add_evaluator, seeded_run
from tests.test_evaluations_api import eid, rid
from tests.test_experiments_api import make, variant


async def completes_only_after_the_lock_is_released(
    engine: AsyncEngine, key: str, call: Callable[[], Coroutine[Any, Any, Any]]
) -> Any:
    async with engine.begin() as holder:
        await holder.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
        task = asyncio.create_task(call())
        await asyncio.sleep(0.6)
        assert not task.done(), f"the request did not wait for the advisory lock {key!r}"
    return await asyncio.wait_for(task, 5)


async def test_evaluator_creation_waits_for_the_workspace_evaluator_lock(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    key = f"ws:{web.tenant.context.workspace_id}:evaluators"

    async def call() -> Any:
        body = {"name": "locked", "kind": "deterministic", "implementation": "run_succeeded"}
        return await web.client.post("/v1/evaluators", json=body, headers=owner)

    assert (
        await completes_only_after_the_lock_is_released(web.engine, key, call)
    ).status_code == 201


async def test_triggers_and_verdicts_wait_for_the_per_run_lock(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")
    machine = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    key = f"eval:{web.tenant.context.workspace_id}:{run}"

    async def trigger() -> Any:
        return await web.client.post(
            f"/v1/runs/{rid(run)}/evaluations", json={"evaluator_id": eid(machine)}, headers=owner
        )

    async def verdict() -> Any:
        return await web.client.post(
            f"/v1/runs/{rid(run)}/verdicts",
            json={"evaluator_id": eid(human), "label": "pass"},
            headers=owner,
        )

    assert (
        await completes_only_after_the_lock_is_released(web.engine, key, trigger)
    ).status_code == 202
    assert (
        await completes_only_after_the_lock_is_released(web.engine, key, verdict)
    ).status_code == 201


async def test_experiment_writes_wait_for_their_locks(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    ws = web.tenant.context.workspace_id

    async def create() -> Any:
        return await make(web, owner, project_id=web.tenant.projects["alpha"])

    created = await completes_only_after_the_lock_is_released(
        web.engine, f"ws:{ws}:experiments", create
    )
    assert created.status_code == 201
    experiment = created.json()["id"]
    key = f"exp:{to_uuid(experiment)}"
    run = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")

    async def add() -> Any:
        return await variant(web, owner, experiment, "b", "baseline", m="1")

    added = await completes_only_after_the_lock_is_released(web.engine, key, add)
    assert added.status_code == 201

    async def attach() -> Any:
        return await web.client.post(
            f"/v1/experiments/{experiment}/runs",
            json={"run_id": from_uuid(IdKind.RUN, run), "variant_id": added.json()["id"]},
            headers=owner,
        )

    assert (
        await completes_only_after_the_lock_is_released(web.engine, key, attach)
    ).status_code == 201

    async def evaluate() -> Any:
        return await web.client.post(f"/v1/experiments/{experiment}/evaluate", headers=owner)

    assert (
        await completes_only_after_the_lock_is_released(web.engine, key, evaluate)
    ).status_code == 202


@pytest.mark.parametrize("bound", ["evaluators"])
async def test_the_lock_keys_are_workspace_scoped(web: Api, bound: str) -> None:
    """The same name in another workspace is another key: one tenant cannot stall another."""
    owner = await person(web, "o@acme.test", "OWNER")
    other_key = f"ws:{web.other.context.workspace_id}:{bound}"

    async def call() -> Any:
        body = {"name": "free", "kind": "deterministic", "implementation": "run_succeeded"}
        return await web.client.post("/v1/evaluators", json=body, headers=owner)

    async with web.engine.begin() as holder:
        await holder.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(other_key, 0)))
        )
        response = await asyncio.wait_for(call(), 3)  # not blocked by the other tenant's key
    assert response.status_code == 201
