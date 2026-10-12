"""Evaluations of a run over HTTP: trigger, verdict, listing, bounds, withholding (AC-3, D7)."""

import asyncio
import uuid
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import text, update

from abb_api.db import tables as t
from abb_api.evaluations.runs import MAX_EVALUATIONS_PER_EVALUATOR, MAX_VERDICTS_PER_RUN
from abb_api.jobs.outbox import EVALUATE_RUN
from tests.api_fixtures import Api, person
from tests.evaluation_helpers import add_evaluator, evaluation_rows, jobs_of, seeded_run, trigger
from tests.test_audit import rows as audit_rows

SECRET = "EXPLCANARY-private-reasoning"


def rid(run: uuid.UUID) -> str:
    return from_uuid(IdKind.RUN, run)


def eid(evaluator: Any) -> str:
    return from_uuid(IdKind.EVALUATOR, evaluator.id)


async def run_in_alpha(web: Api, terminal: str = "success") -> uuid.UUID:
    return await seeded_run(web.engine, web.tenant, web.worker, terminal=terminal, project="alpha")


async def start(web: Api, headers: dict[str, str], run: uuid.UUID, evaluator: Any) -> Any:
    return await web.client.post(
        f"/v1/runs/{rid(run)}/evaluations",
        json={"evaluator_id": eid(evaluator)},
        headers=headers,
    )


async def verdict(
    web: Api, headers: dict[str, str], run: uuid.UUID, evaluator: Any, **body: Any
) -> Any:
    body.setdefault("label", "pass")
    return await web.client.post(
        f"/v1/runs/{rid(run)}/verdicts",
        json={"evaluator_id": eid(evaluator), **body},
        headers=headers,
    )


async def listing(web: Api, headers: dict[str, str], run: uuid.UUID) -> list[dict[str, Any]]:
    response = await web.client.get(f"/v1/runs/{rid(run)}/evaluations", headers=headers)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def test_a_trigger_is_pending_then_completes_and_the_run_is_untouched(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    before = await web.client.get(f"/v1/runs/{rid(run)}", headers=owner)

    made = await start(web, owner, run, evaluator)
    assert made.status_code == 202, made.text
    body = made.json()
    assert body["state"] == "pending" and body["kind"] == "deterministic"
    assert body["ground_truth"] is True and body["latest"] is True
    assert body["evaluator_id"] == eid(evaluator) and body["evaluator_name"] == "ok"

    await web.drain()
    (done,) = await listing(web, owner, run)
    assert done["id"] == body["id"] and done["state"] == "completed"
    assert done["score"] == 1.0 and done["label"] and done["explanation"]
    assert done["error_code"] is None and done["completed_at"] is not None
    after = await web.client.get(f"/v1/runs/{rid(run)}", headers=owner)
    assert after.json() == before.json()  # evaluating never changes the run


async def test_a_repeat_while_pending_returns_the_same_row_and_creates_no_job(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    first = await start(web, owner, run, evaluator)
    jobs_before = len(await jobs_of(web.engine, EVALUATE_RUN))
    second = await start(web, owner, run, evaluator)
    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["id"] == first.json()["id"] and second.json()["state"] == "pending"
    assert len(await jobs_of(web.engine, EVALUATE_RUN)) == jobs_before
    assert len(await evaluation_rows(web.engine, run)) == 1


async def test_after_completion_a_trigger_inserts_a_second_row_and_leaves_the_first(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    first = (await start(web, owner, run, evaluator)).json()
    await web.drain()
    (completed,) = await evaluation_rows(web.engine, run)
    second = await start(web, owner, run, evaluator)
    assert second.status_code == 202 and second.json()["id"] != first["id"]
    await web.drain()
    items = await listing(web, owner, run)
    assert [i["latest"] for i in items] == [True, False]  # newest first
    assert items[1]["id"] == first["id"]
    unchanged = {r.id: r for r in await evaluation_rows(web.engine, run)}[completed.id]
    assert tuple(unchanged) == tuple(completed)


async def test_a_requeued_job_for_a_terminal_row_changes_nothing(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    await start(web, owner, run, evaluator)
    await web.drain()
    before = [tuple(r) for r in await evaluation_rows(web.engine, run)]
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE outbox_jobs SET status = 'pending', available_at = now() "
                 "WHERE job_type = 'evaluate_run'")
        )  # fmt: skip
    await web.drain()
    assert [tuple(r) for r in await evaluation_rows(web.engine, run)] == before


async def test_the_twenty_first_result_for_an_evaluator_is_a_conflict(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    for _ in range(MAX_EVALUATIONS_PER_EVALUATOR):
        assert (await start(web, owner, run, evaluator)).status_code == 202
        await web.drain()
    over = await start(web, owner, run, evaluator)
    assert over.status_code == 409 and over.json()["error"]["code"] == "LIMIT_REACHED"
    assert over.json()["error"]["details"]["limit"] == "evaluations_per_evaluator"
    assert len(await evaluation_rows(web.engine, run)) == MAX_EVALUATIONS_PER_EVALUATOR
    other = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="other")
    assert (await start(web, owner, run, other)).status_code == 202  # the bound is per evaluator


async def test_thirty_concurrent_triggers_at_nineteen_rows_add_exactly_one(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    for _ in range(MAX_EVALUATIONS_PER_EVALUATOR - 1):
        await trigger(web.engine, web.tenant, run, evaluator)
        await web.drain()
    assert len(await evaluation_rows(web.engine, run)) == MAX_EVALUATIONS_PER_EVALUATOR - 1
    responses = await asyncio.gather(*[start(web, owner, run, evaluator) for _ in range(30)])
    assert sorted(r.status_code for r in responses) == [200] * 29 + [202]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(await evaluation_rows(web.engine, run)) == MAX_EVALUATIONS_PER_EVALUATOR
    await web.drain()
    over = await start(web, owner, run, evaluator)
    assert over.status_code == 409 and over.json()["error"]["code"] == "LIMIT_REACHED"


async def test_a_human_evaluator_cannot_be_triggered_and_a_verdict_needs_one(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    machine = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    refused = await start(web, owner, run, human)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "EVALUATOR_KIND_MISMATCH"
    wrong = await verdict(web, owner, run, machine)
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "EVALUATOR_KIND_MISMATCH"
    assert await evaluation_rows(web.engine, run) == []


async def test_a_verdict_is_completed_at_insert_and_audited_without_its_explanation(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    made = await verdict(web, owner, run, human, label="fail", score=0.25, explanation=SECRET)
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["state"] == "completed" and body["kind"] == "human" and body["ground_truth"]
    assert (body["label"], body["score"], body["explanation"]) == ("fail", 0.25, SECRET)
    (row,) = await evaluation_rows(web.engine, run)
    assert row.state == "completed" and row.requested_by is not None
    entries = [e for e in await audit_rows(web) if e.action == "evaluation.label"]
    assert len(entries) == 1
    assert entries[0].details["label"] == "fail" and entries[0].details["run_id"] == rid(run)
    assert SECRET not in repr(entries[0])
    # A correction is a newer row; the first stays.
    again = await verdict(web, owner, run, human, label="pass")
    assert again.status_code == 201
    items = await listing(web, owner, run)
    assert [(i["label"], i["latest"]) for i in items] == [("pass", True), ("fail", False)]


@pytest.mark.parametrize(
    "body",
    [
        {"label": "maybe"},  # not one of the evaluator's labels
        {"label": ""},
        {"label": "pass", "score": 1.5},
        {"label": "pass", "score": "nan"},
        {"label": "pass", "explanation": "x" * 4_001},
        {"label": "pass", "explanation": "bad\x00nul"},
        {"label": "pass", "extra": 1},
    ],
)
async def test_an_invalid_verdict_is_a_422_before_the_database(
    web: Api, body: dict[str, Any]
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    response = await web.client.post(
        f"/v1/runs/{rid(run)}/verdicts", json={"evaluator_id": eid(human), **body}, headers=owner
    )
    assert response.status_code == 422, response.text
    assert await evaluation_rows(web.engine, run) == []


async def test_a_four_byte_explanation_of_four_thousand_characters_is_accepted(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    ok = await verdict(web, owner, run, human, explanation="\U0001d4d0" * 4_000)  # 16,000 bytes
    assert ok.status_code == 201, ok.text[:300]


async def test_the_fifty_first_verdict_is_a_conflict(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    for _ in range(MAX_VERDICTS_PER_RUN):
        assert (await verdict(web, owner, run, human)).status_code == 201
    over = await verdict(web, owner, run, human)
    assert over.status_code == 409 and over.json()["error"]["details"]["limit"] == "verdicts"
    other = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")
    assert (await verdict(web, owner, other, human)).status_code == 201  # per run


async def test_an_evaluator_of_another_project_is_a_422_and_an_archived_one_a_409(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    beta = await add_evaluator(web.engine, web.tenant, name="beta-only", project="beta")
    alpha = await add_evaluator(web.engine, web.tenant, name="alpha-only", project="alpha")
    wide = await add_evaluator(web.engine, web.tenant, name="wide")
    mismatch = await start(web, owner, run, beta)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "EVALUATOR_PROJECT_MISMATCH"
    assert (await start(web, owner, run, alpha)).status_code == 202
    assert (await start(web, owner, run, wide)).status_code == 202
    archived = await web.client.delete(f"/v1/evaluators/{eid(wide)}", headers=owner)
    assert archived.status_code == 204
    again = await start(web, owner, run, wide)
    assert again.status_code == 409 and again.json()["error"]["code"] == "EVALUATOR_ARCHIVED"


async def test_a_judge_evaluator_is_refused_while_no_provider_is_configured(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    judge = await add_evaluator(web.engine, web.tenant, "judge", kind="llm_judge", name="j")
    response = await start(web, owner, run, judge)
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "JUDGE_NOT_CONFIGURED"
    )
    assert await evaluation_rows(web.engine, run) == []


async def test_foreign_and_random_ids_give_identical_404s(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    mine = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    theirs = await add_evaluator(web.engine, web.other, name="theirs")
    foreign_run = await seeded_run(web.engine, web.other, web.worker)
    random_run, random_evaluator = uuid.uuid4(), uuid.uuid4()

    def clean(response: Any) -> dict[str, Any]:
        return {**response.json()["error"], "request_id": None}

    for path_run, evaluator in ((foreign_run, mine), (random_run, mine)):
        r = await start(web, owner, path_run, evaluator)
        assert r.status_code == 404 and r.json()["error"]["code"] == "RUN_NOT_FOUND"
    a = await start(web, owner, run, theirs)
    b = await web.client.post(
        f"/v1/runs/{rid(run)}/evaluations",
        json={"evaluator_id": from_uuid(IdKind.EVALUATOR, random_evaluator)},
        headers=owner,
    )
    assert a.status_code == b.status_code == 404
    assert clean(a) == clean(b) and clean(a)["code"] == "EVALUATOR_NOT_FOUND"
    listed = await web.client.get(f"/v1/runs/{rid(foreign_run)}/evaluations", headers=owner)
    assert listed.status_code == 404 and listed.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_a_pending_row_whose_job_was_dead_lettered_reads_as_failed_without_a_write(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    (stuck,) = await trigger(web.engine, web.tenant, run, evaluator)
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE outbox_jobs SET status = 'dead_letter' WHERE job_type = 'evaluate_run'")
        )
    (item,) = await listing(web, owner, run)
    assert (item["state"], item["error_code"]) == ("failed", "JOB_FAILED")
    (row,) = await evaluation_rows(web.engine, run)
    assert row.id == stuck and row.state == "pending"  # derived, not written
    # A new trigger is not blocked by the dead row: it inserts a fresh pending one.
    fresh = await start(web, owner, run, evaluator)
    assert fresh.status_code == 202 and fresh.json()["id"] != from_uuid(IdKind.EVALUATION, stuck)
    # The dead row is now failed for real; the new job runs only the fresh row.
    await web.drain()
    first, second = await evaluation_rows(web.engine, run)
    assert (first.id, first.state, first.error_code) == (stuck, "failed", "JOB_FAILED")
    assert second.state == "completed"
    states = [(i["state"], i["error_code"]) for i in await listing(web, owner, run)]
    assert sorted(states, key=str) == [("completed", None), ("failed", "JOB_FAILED")]


async def test_a_pending_row_with_no_job_is_failed_only_after_the_stale_limit(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    await trigger(web.engine, web.tenant, run, evaluator)
    async with web.engine.begin() as conn:
        await conn.execute(text("DELETE FROM outbox_jobs WHERE job_type = 'evaluate_run'"))
    (young,) = await listing(web, owner, run)
    assert young["state"] == "pending"
    async with web.engine.begin() as conn:
        await conn.execute(
            update(t.evaluations).values(created_at=text("now() - interval '2 hours'"))
        )
    (old,) = await listing(web, owner, run)
    assert (old["state"], old["error_code"]) == ("failed", "JOB_FAILED")


async def test_a_viewer_sees_scores_and_labels_but_never_free_text(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    run = await run_in_alpha(web)
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    machine = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    judge = await add_evaluator(web.engine, web.tenant, "judge", kind="llm_judge", name="j")
    await verdict(web, owner, run, human, label="fail", score=0.1, explanation=SECRET)
    await start(web, owner, run, machine)
    await web.drain()
    async with web.engine.begin() as conn:  # a completed judge row, as the worker would leave it
        await conn.execute(
            t.evaluations.insert().values(
                workspace_id=web.tenant.context.workspace_id, id=uuid.uuid4(), run_id=run,
                evaluator_id=judge.id, evaluator_name="j", evaluator_version="1",
                kind="llm_judge", implementation="judge", state="completed", score=0.5,
                label="ok", explanation=SECRET,
                metadata={"raw": SECRET, "judge_model": "fake-judge-1", "cost_usd": 0},
            )
        )  # fmt: skip

    def by_kind(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {i["kind"]: i for i in items}

    seen = by_kind(await listing(web, viewer, run))
    assert SECRET not in repr(seen)
    assert seen["human"]["label"] == "fail" and seen["human"]["score"] == 0.1
    assert seen["human"]["explanation"] is None and seen["human"]["explanation_withheld"] is True
    assert seen["llm_judge"]["ground_truth"] is False
    assert seen["llm_judge"]["metadata"] == {"judge_model": "fake-judge-1", "cost_usd": 0}
    assert seen["llm_judge"]["metadata_withheld"] == ["raw"]
    assert seen["deterministic"]["explanation"]  # templated numbers only: visible
    assert seen["deterministic"]["explanation_withheld"] is False

    full = by_kind(await listing(web, owner, run))
    assert full["human"]["explanation"] == SECRET and full["llm_judge"]["metadata"]["raw"] == SECRET
    assert full["human"]["explanation_withheld"] is False


async def test_keys_and_roles_without_the_actions_are_refused(web: Api) -> None:
    viewer = await person(web, "v@acme.test", "VIEWER")
    billing = await person(web, "b@acme.test", "BILLING")
    run = await run_in_alpha(web)
    machine = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    assert (await start(web, viewer, run, machine)).status_code == 403
    assert (await verdict(web, viewer, run, human)).status_code == 403
    listed = await web.client.get(f"/v1/runs/{rid(run)}/evaluations", headers=billing)
    assert listed.status_code == 403
    key = web.headers("reader")
    assert (
        await web.client.get(f"/v1/runs/{rid(run)}/evaluations", headers=key)
    ).status_code == 200
    posted = await web.client.post(
        f"/v1/runs/{rid(run)}/evaluations", json={"evaluator_id": eid(machine)}, headers=key
    )
    assert posted.status_code == 403
    assert await evaluation_rows(web.engine, run) == []


async def test_the_evaluation_reads_never_cross_the_tenant_even_with_a_foreign_run_id(
    web: Api,
) -> None:
    """INV-3 below the HTTP layer: every read carries the workspace, whatever run id it is given."""
    from abb_api.evaluations.repository import EvaluationReads

    foreign_run = await seeded_run(web.engine, web.other, web.worker)
    foreign = await add_evaluator(web.engine, web.other, "run_succeeded", name="theirs")
    await trigger(web.engine, web.other, foreign_run, foreign)
    await web.drain()
    assert len(await evaluation_rows(web.engine, foreign_run)) == 1
    async with web.engine.connect() as conn:
        mine = EvaluationReads(conn, web.tenant.context)
        assert await mine.latest_per_evaluator([foreign_run]) == []
        assert await mine.list_for_run(foreign_run, 10) == []
        assert await mine.open_rows(foreign_run, foreign.id) == []
        assert await mine.count_for_evaluator(foreign_run, foreign.id) == 0
        theirs = EvaluationReads(conn, web.other.context)
        assert len(await theirs.latest_per_evaluator([foreign_run])) == 1


async def test_a_viewer_never_sees_the_error_text_of_a_failed_row(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    run = await run_in_alpha(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    async with web.engine.begin() as conn:
        await conn.execute(
            t.evaluations.insert().values(
                workspace_id=web.tenant.context.workspace_id, id=uuid.uuid4(), run_id=run,
                evaluator_id=evaluator.id, evaluator_name="ok", evaluator_version="1",
                kind="deterministic", implementation="run_succeeded", state="failed",
                error_code="EVALUATOR_ERROR", error=SECRET,
            )
        )  # fmt: skip
    (seen,) = await listing(web, viewer, run)
    assert seen["error_code"] == "EVALUATOR_ERROR" and seen["error"] is None
    assert SECRET not in repr(seen)
    (full,) = await listing(web, owner, run)
    assert full["error"] == SECRET


async def test_claim_pending_never_claims_another_workspaces_rows(web: Api) -> None:
    """INV-3 below the HTTP layer: the claim carries the workspace, whatever run id it is given."""
    from abb_api.evaluations.repository import EvaluationRepository

    foreign_run = await seeded_run(web.engine, web.other, web.worker)
    foreign = await add_evaluator(web.engine, web.other, "run_succeeded", name="theirs")
    await trigger(web.engine, web.other, foreign_run, foreign)  # pending, not drained
    async with web.engine.begin() as conn:
        assert (
            await EvaluationRepository(conn, web.tenant.context).claim_pending(foreign_run, 10)
            == []
        )
        assert (
            len(await EvaluationRepository(conn, web.other.context).claim_pending(foreign_run, 10))
            == 1
        )
