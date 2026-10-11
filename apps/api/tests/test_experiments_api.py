"""Experiments over HTTP: variants and the control check, bounds, metrics, evaluate-all (AC-8)."""

import asyncio
import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid, to_uuid
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.ids import new_uuid
from abb_api.jobs.outbox import EVALUATE_RUN
from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.conftest import make_settings
from tests.evaluation_helpers import add_evaluator, evaluation_rows, jobs_of, seeded_run
from tests.test_audit import rows as audit_rows

T0 = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
async def fast(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    """The `web` fixture with no batch delay, so a drain runs the evaluation jobs."""
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, eval_batch_delay_seconds=0)
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


def pid(api: Api, slug: str = "alpha") -> str:
    return api.tenant.projects[slug]


async def make(api: Api, headers: dict[str, str], **body: Any) -> Any:
    body.setdefault("name", "Prompt v2 vs v1")
    body.setdefault("project_id", pid(api))
    return await api.client.post("/v1/experiments", json=body, headers=headers)


async def variant(
    api: Api, headers: dict[str, str], experiment: str, name: str, role: str, **config: str
) -> Any:
    return await api.client.post(
        f"/v1/experiments/{experiment}/variants",
        json={"name": name, "role": role, "config": config},
        headers=headers,
    )


async def attach(api: Api, headers: dict[str, str], experiment: str, run: Any, var: str) -> Any:
    return await api.client.post(
        f"/v1/experiments/{experiment}/runs",
        json={"run_id": from_uuid(IdKind.RUN, run), "variant_id": var},
        headers=headers,
    )


async def detail(api: Api, headers: dict[str, str], experiment: str) -> dict[str, Any]:
    response = await api.client.get(f"/v1/experiments/{experiment}", headers=headers)
    assert response.status_code == 200, response.text[:300]
    body: dict[str, Any] = response.json()
    return body


async def bulk_runs(api: Api, count: int, project: str = "alpha") -> list[uuid.UUID]:
    """Run rows without events: enough for membership, bounds and the metrics read-model."""
    ids = [new_uuid(IdKind.RUN) for _ in range(count)]
    rows = [
        {
            "workspace_id": api.tenant.context.workspace_id, "id": i,
            "project_id": api.tenant.project_uuids[project], "trace_id": new_uuid(IdKind.TRACE),
            "status": "SUCCESS", "started_at": T0,
        }
        for i in ids
    ]  # fmt: skip
    async with api.engine.begin() as conn:
        await conn.execute(insert(t.runs), rows)
    return ids


async def link(api: Api, experiment: str, variant_id: str, runs: list[uuid.UUID]) -> None:
    ws = api.tenant.context.workspace_id
    async with api.engine.begin() as conn:
        await conn.execute(
            insert(t.experiment_runs),
            [
                {"workspace_id": ws, "experiment_id": to_uuid(experiment),
                 "run_id": r, "variant_id": to_uuid(variant_id)}
                for r in runs
            ],
        )  # fmt: skip


async def test_an_experiment_with_a_baseline_and_candidates_and_its_control_check(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    made = await make(web, owner, description="two prompts", dataset_label="session-bugs")
    assert made.status_code == 201, made.text
    experiment = made.json()["id"]
    assert experiment.startswith("exp_") and made.json()["variants"] == []

    early = await variant(web, owner, experiment, "v2", "candidate", model="m1")
    assert early.status_code == 409 and early.json()["error"]["code"] == "BASELINE_REQUIRED"
    base = await variant(web, owner, experiment, "v1", "baseline", model="m1", prompt="a")
    assert base.status_code == 201 and base.json()["controlled"] is None
    second = await variant(web, owner, experiment, "v1b", "baseline", model="m1")
    assert second.status_code == 409 and second.json()["error"]["code"] == "BASELINE_EXISTS"
    taken = await variant(web, owner, experiment, "v1", "candidate", model="m2")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "VARIANT_NAME_TAKEN"

    same = await variant(web, owner, experiment, "same", "candidate", model="m1", prompt="a")
    one = await variant(web, owner, experiment, "one", "candidate", model="m1", prompt="b")
    two = await variant(web, owner, experiment, "two", "candidate", model="m2", prompt="b")
    missing = await variant(web, owner, experiment, "missing", "candidate", model="m1")
    extra = await variant(
        web, owner, experiment, "extra", "candidate", model="m1", prompt="a", t="1"
    )
    got = {r.json()["name"]: r.json() for r in (same, one, two, missing, extra)}
    assert (got["same"]["changed_variables"], got["same"]["controlled"]) == ([], False)
    assert (got["one"]["changed_variables"], got["one"]["controlled"]) == (["prompt"], True)
    assert got["two"]["changed_variables"] == ["model", "prompt"] and not got["two"]["controlled"]
    assert got["missing"]["changed_variables"] == ["prompt"]  # a missing key counts
    assert got["extra"]["changed_variables"] == ["t"] and got["extra"]["controlled"] is True

    body = await detail(web, owner, experiment)
    codes = [(w["code"], w["variant_id"] is not None) for w in body["warnings"]]
    flagged = {w["variant_id"] for w in body["warnings"] if w["code"] == "not_single_variable"}
    assert flagged == {got["same"]["id"], got["two"]["id"]}
    assert ("not_single_variable", True) in codes
    assert body["variants"][0]["role"] == "baseline" and len(body["variants"]) == 6
    listed = (await web.client.get("/v1/experiments", headers=owner)).json()["items"]
    assert [(e["id"], e["variant_count"], e["run_count"]) for e in listed] == [(experiment, 6, 0)]


async def test_variant_configs_are_flat_labels_with_bounds(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    experiment = (await make(web, owner)).json()["id"]
    url = f"/v1/experiments/{experiment}/variants"
    for bad in (
        {"name": "v", "role": "baseline", "config": {"a": "x" * 257}},
        {"name": "v", "role": "baseline", "config": {f"k{n}": "x" for n in range(33)}},
        {"name": "v", "role": "baseline", "config": {"nested": {"a": "b"}}},
        {"name": "v", "role": "baseline", "config": {"a": 1}},
        {"name": "bad name", "role": "baseline", "config": {}},
        {"name": "v", "role": "other", "config": {}},
        {"name": "v", "role": "baseline", "config": {f"k{n}": "x" * 256 for n in range(32)}},
    ):  # the last one: 32 maximal values are over the 8 KiB total
        response = await web.client.post(url, json=bad, headers=owner)
        assert response.status_code == 422, bad
    ok = await web.client.post(
        url,
        json={"name": "v", "role": "baseline", "config": {f"k{n}": "x" * 200 for n in range(32)}},
        headers=owner,
    )
    assert ok.status_code == 201, ok.text[:200]


async def test_the_eleventh_variant_is_a_conflict_and_concurrent_adds_cannot_overshoot(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    experiment = (await make(web, owner)).json()["id"]
    assert (await variant(web, owner, experiment, "b", "baseline", m="0")).status_code == 201
    for n in range(8):
        assert (
            await variant(web, owner, experiment, f"c{n}", "candidate", m=str(n))
        ).status_code == 201
    racers = await asyncio.gather(
        *[variant(web, owner, experiment, f"r{n}", "candidate", m="x") for n in range(6)]
    )
    assert sorted(r.status_code for r in racers) == [201] + [409] * 5  # exactly one fits
    assert all(
        r.json()["error"]["details"]["limit"] == "variants" for r in racers if r.status_code == 409
    )
    assert len((await detail(web, owner, experiment))["variants"]) == 10


async def test_attaching_runs_checks_project_variant_uniqueness_and_the_bound(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    experiment = (await make(web, owner)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    cand = (await variant(web, owner, experiment, "c", "candidate", m="2")).json()["id"]
    inside = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")
    outside = await seeded_run(web.engine, web.tenant, web.worker, project="beta")

    first = await attach(web, owner, experiment, inside, base)
    assert first.status_code == 201 and first.json()["status"] == "SUCCESS"
    again = await attach(web, owner, experiment, inside, base)
    assert again.status_code == 200 and again.json()["added_at"] == first.json()["added_at"]
    other = await attach(web, owner, experiment, inside, cand)
    assert other.status_code == 409 and other.json()["error"]["code"] == "RUN_ALREADY_ATTACHED"
    wrong_project = await attach(web, owner, experiment, outside, base)
    assert wrong_project.status_code == 422
    assert wrong_project.json()["error"]["code"] == "RUN_PROJECT_MISMATCH"
    unknown = await attach(web, owner, experiment, uuid.uuid4(), base)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "RUN_NOT_FOUND"
    no_variant = await web.client.post(
        f"/v1/experiments/{experiment}/runs",
        json={
            "run_id": from_uuid(IdKind.RUN, inside),
            "variant_id": from_uuid(IdKind.VARIANT, uuid.uuid4()),
        },
        headers=owner,
    )
    assert (
        no_variant.status_code == 404 and no_variant.json()["error"]["code"] == "VARIANT_NOT_FOUND"
    )

    gone = await web.client.delete(
        f"/v1/experiments/{experiment}/runs/{from_uuid(IdKind.RUN, inside)}", headers=owner
    )
    assert gone.status_code == 204
    again_gone = await web.client.delete(
        f"/v1/experiments/{experiment}/runs/{from_uuid(IdKind.RUN, inside)}", headers=owner
    )
    assert again_gone.status_code == 404 and again_gone.json()["error"]["code"] == "RUN_NOT_FOUND"

    filled = await bulk_runs(web, 500)
    await link(web, experiment, base, filled)
    over = await attach(web, owner, experiment, inside, base)
    assert over.status_code == 409 and over.json()["error"]["details"]["limit"] == "runs"


async def test_evaluators_are_validated_at_creation(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    ok = await add_evaluator(web.engine, web.tenant, name="ok")
    beta = await add_evaluator(web.engine, web.tenant, name="beta-only", project="beta")

    def ref(e: Any) -> str:
        return from_uuid(IdKind.EVALUATOR, e.id)

    fine = await make(web, owner, evaluator_ids=[ref(ok), ref(ok)])
    assert fine.status_code == 201 and [e["name"] for e in fine.json()["evaluators"]] == ["ok"]
    mismatch = await make(web, owner, evaluator_ids=[ref(beta)])
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "EVALUATOR_PROJECT_MISMATCH"
    unknown = await make(web, owner, evaluator_ids=[from_uuid(IdKind.EVALUATOR, uuid.uuid4())])
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "EVALUATOR_NOT_FOUND"
    many = await make(
        web, owner, evaluator_ids=[from_uuid(IdKind.EVALUATOR, uuid.uuid4()) for _ in range(21)]
    )
    assert many.status_code == 422
    foreign = await add_evaluator(web.engine, web.other, name="theirs")
    stranger = await make(web, owner, evaluator_ids=[ref(foreign)])
    assert stranger.status_code == 404 and stranger.json()["error"]["code"] == "EVALUATOR_NOT_FOUND"
    wrong_project = await make(web, owner, project_id=from_uuid(IdKind.PROJECT, uuid.uuid4()))
    assert wrong_project.status_code == 404


async def test_the_two_hundred_first_experiment_is_a_conflict(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    ws = web.tenant.context.workspace_id
    async with web.engine.begin() as conn:
        await conn.execute(
            insert(t.experiments),
            [
                {"workspace_id": ws, "id": new_uuid(IdKind.EXPERIMENT),
                 "project_id": web.tenant.project_uuids["alpha"], "name": f"e{n}"}
                for n in range(200)
            ],
        )  # fmt: skip
    over = await make(web, owner)
    assert over.status_code == 409 and over.json()["error"]["details"]["limit"] == "experiments"
    first = (await web.client.get("/v1/experiments", headers=owner)).json()["items"][0]["id"]
    assert (await web.client.delete(f"/v1/experiments/{first}", headers=owner)).status_code == 204
    assert (await make(web, owner)).status_code == 201  # an archived one frees its place


async def test_an_archived_experiment_is_readable_and_takes_no_changes(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    experiment = (await make(web, owner)).json()["id"]
    assert (
        await web.client.delete(f"/v1/experiments/{experiment}", headers=owner)
    ).status_code == 204
    assert (
        await web.client.delete(f"/v1/experiments/{experiment}", headers=owner)
    ).status_code == 204
    assert (await detail(web, owner, experiment))["archived_at"] is not None
    refused = await variant(web, owner, experiment, "b", "baseline", m="1")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "EXPERIMENT_ARCHIVED"
    hidden = (await web.client.get("/v1/experiments", headers=owner)).json()["items"]
    shown = (await web.client.get("/v1/experiments?include_archived=true", headers=owner)).json()
    assert hidden == [] and len(shown["items"]) == 1


# ------------------------------------------------------------------ metrics


def percentile(values: list[float], p: float) -> float | None:
    """`percentile_cont`: linear interpolation between the closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def seed_metrics(api: Api) -> tuple[str, dict[str, list[dict[str, Any]]], list[Any]]:
    """Two variants with controlled run figures and evaluation rows; returns the expectation
    inputs (the same numbers the test recomputes from D5's definitions)."""
    owner = await person(api, "m@acme.test", "OWNER")
    ok = await add_evaluator(api.engine, api.tenant, name="ok")
    tests = await add_evaluator(api.engine, api.tenant, name="tests")
    refs = [from_uuid(IdKind.EVALUATOR, e.id) for e in (ok, tests)]
    experiment = (await make(api, owner, evaluator_ids=refs)).json()["id"]
    base = (await variant(api, owner, experiment, "v1", "baseline", model="m1")).json()["id"]
    cand = (await variant(api, owner, experiment, "v2", "candidate", model="m2")).json()["id"]
    figures = {
        base: [
            {
                "status": "SUCCESS",
                "cost": "0.10",
                "ms": 1000.0,
                "retry": 0,
                "tools": 4,
                "errors": 0,
            },
            {
                "status": "SUCCESS",
                "cost": "0.30",
                "ms": 3000.0,
                "retry": 2,
                "tools": 8,
                "errors": 2,
            },
            {"status": "FAILED", "cost": "0.20", "ms": 2500.0, "retry": 1, "tools": 6, "errors": 3},
            {
                "status": "TIMED_OUT",
                "cost": "0.90",
                "ms": 9000.0,
                "retry": 0,
                "tools": 9,
                "errors": 1,
            },
            {
                "status": "RUNNING",
                "cost": "5.00",
                "ms": 99999.0,
                "retry": 9,
                "tools": 99,
                "errors": 0,
            },
        ],
        cand: [
            {"status": "SUCCESS", "cost": "0.05", "ms": 800.0, "retry": 0, "tools": 3, "errors": 0},
            {"status": "SUCCESS", "cost": "0.07", "ms": 900.0, "retry": 0, "tools": 3, "errors": 0},
            {
                "status": "SUCCESS",
                "cost": "0.40",
                "ms": 4000.0,
                "retry": 1,
                "tools": 7,
                "errors": 0,
            },
        ],
    }
    ws = api.tenant.context.workspace_id
    by_variant: dict[str, list[uuid.UUID]] = {}
    for var, rows in figures.items():
        runs = await bulk_runs(api, len(rows))
        by_variant[var] = runs
        await link(api, experiment, var, runs)
        async with api.engine.begin() as conn:
            for run, f in zip(runs, rows, strict=True):
                await conn.execute(
                    update(t.runs)
                    .where(t.runs.c.workspace_id == ws, t.runs.c.id == run)
                    .values(
                        status=f["status"], cost_usd=f["cost"], duration_ms=f["ms"],
                        retry_count=f["retry"], tool_calls=f["tools"],
                        summary={
                            "error_count": f["errors"],
                            "models": ["m2" if var == cand else "m1"],
                        },
                        agent_slug="agent-a",
                    )
                )  # fmt: skip
    # Evaluation rows: (variant, run index, evaluator, state, score, label, minutes after T0)
    plan = [
        (base, 0, ok, "completed", 1.0, "pass", 1),
        (base, 0, ok, "completed", 0.0, "fail", 2),  # newer: this is the latest for the pair
        (base, 1, ok, "completed", 1.0, "pass", 1),
        (base, 2, ok, "failed", None, None, 1),
        (base, 2, ok, "completed", 0.5, "pass", 0),  # older than the failure: the failure wins
        (base, 3, ok, "pending", None, None, 3),  # not terminal: ignored
        (base, 0, tests, "completed", 1.0, "pass", 1),
        (cand, 0, ok, "completed", 1.0, "pass", 1),
        (cand, 1, ok, "completed", 1.0, "pass", 1),
        (cand, 2, ok, "completed", 0.25, "fail", 1),
        (cand, 2, tests, "completed", 0.75, "pass", 1),
    ]
    async with api.engine.begin() as conn:
        for var, index, evaluator, state, score, label, minutes in plan:
            await conn.execute(
                insert(t.evaluations).values(
                    workspace_id=ws, id=new_uuid(IdKind.EVALUATION), run_id=by_variant[var][index],
                    evaluator_id=evaluator.id, evaluator_name=evaluator.name, evaluator_version="1",
                    kind="deterministic", implementation="run_succeeded", state=state, score=score,
                    label=label, explanation="" if state == "completed" else None,
                    created_at=T0 + timedelta(minutes=minutes),
                )
            )  # fmt: skip
    return experiment, figures, [ok, tests]


async def test_metrics_equal_a_recomputation_from_the_definitions(web: Api) -> None:
    experiment, figures, (ok, tests) = await seed_metrics(web)
    body = await detail(web, (await person(web, "r@acme.test", "VIEWER")), experiment)
    metrics = {m["variant_id"]: m for m in body["metrics"]}
    terminal = {"SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT", "BLOCKED"}
    for variant_id, rows in figures.items():
        got = metrics[variant_id]
        done = [r for r in rows if r["status"] in terminal]
        assert (got["runs"], got["finished"]) == (len(rows), len(done))
        assert got["success_rate"] == sum(r["status"] == "SUCCESS" for r in done) / len(done)
        for field, key in (("cost_usd", "cost"), ("duration_ms", "ms")):
            values = [float(r[key]) for r in done]
            assert got[field]["mean"] == pytest.approx(mean(values))
            assert got[field]["median"] == pytest.approx(percentile(values, 0.5))
            assert got[field]["p95"] == pytest.approx(percentile(values, 0.95))
        assert got["retry_rate"] == sum(r["retry"] > 0 for r in done) / len(done)
        assert got["tool_calls_mean"] == pytest.approx(mean([float(r["tools"]) for r in done]))
        expected: dict[str, int] = {}
        for r in done:
            if r["status"] != "SUCCESS":
                expected[r["status"].lower()] = expected.get(r["status"].lower(), 0) + 1
            elif r["errors"] > 0:
                expected["recovered"] = expected.get("recovered", 0) + 1
        assert got["failure_categories"] == dict(sorted(expected.items()))
    ok_id, tests_id = (from_uuid(IdKind.EVALUATOR, e.id) for e in (ok, tests))
    base, cand = list(figures)
    by_evaluator = {(v, e["evaluator_id"]): e for v, m in metrics.items() for e in m["evaluations"]}
    # base/ok: run0 latest fail(0.0), run1 pass(1.0), run2 latest failed -> 2 completed, 1 failed
    e = by_evaluator[(base, ok_id)]
    assert (e["completed"], e["failed"]) == (2, 1)
    assert e["mean_score"] == pytest.approx(0.5) and e["pass_rate"] == pytest.approx(0.5)
    e = by_evaluator[(base, tests_id)]
    assert (e["completed"], e["failed"], e["mean_score"], e["pass_rate"]) == (1, 0, 1.0, 1.0)
    e = by_evaluator[(cand, ok_id)]
    assert e["completed"] == 3 and e["mean_score"] == pytest.approx(0.75)
    assert e["pass_rate"] == pytest.approx(2 / 3)
    assert (cand, tests_id) in by_evaluator
    unevaluated = [w for w in body["warnings"] if w["code"] == "unevaluated_runs"]
    assert unevaluated and unevaluated[0]["count"] == (5 + 3) * 2 - 8  # runs x evaluators - results


async def test_metrics_over_five_hundred_runs_and_twenty_evaluators_stay_under_the_timeout(
    web: Api,
) -> None:
    """AC-7: the read-model statement at the experiment bounds (numbers in the benchmark file)."""
    owner = await person(web, "o@acme.test", "OWNER")
    evaluators = [
        await add_evaluator(web.engine, web.tenant, "run_succeeded", name=f"e{n}")
        for n in range(20)
    ]
    refs = [from_uuid(IdKind.EVALUATOR, e.id) for e in evaluators]
    experiment = (await make(web, owner, evaluator_ids=refs)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    cand = (await variant(web, owner, experiment, "c", "candidate", m="2")).json()["id"]
    runs = await bulk_runs(web, 500)
    await link(web, experiment, base, runs[:250])
    await link(web, experiment, cand, runs[250:])
    ws = web.tenant.context.workspace_id
    rows = [
        {"workspace_id": ws, "id": new_uuid(IdKind.EVALUATION), "run_id": run,
         "evaluator_id": e.id, "evaluator_name": e.name, "evaluator_version": "1",
         "kind": "deterministic", "state": "completed", "score": (n % 5) / 4, "label": "pass",
         "explanation": "", "created_at": T0 + timedelta(minutes=n)}
        for n, run in enumerate(runs) for e in evaluators
    ]  # fmt: skip
    async with web.engine.begin() as conn:
        await conn.execute(insert(t.evaluations), rows)
    import time

    times = []
    for _ in range(5):
        start = time.perf_counter()
        body = await detail(web, owner, experiment)
        times.append(time.perf_counter() - start)
    print(f"experiment detail, 500 runs x 20 evaluators: {sorted(times)[2] * 1000:.0f} ms median")
    assert max(times) < web.app.state.settings.analytics_timeout_seconds
    assert {len(m["evaluations"]) for m in body["metrics"]} == {20}
    assert sum(m["runs"] for m in body["metrics"]) == 500


async def test_models_and_agents_that_vary_inside_a_variant_are_flagged(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    experiment = (await make(web, owner)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    runs = await bulk_runs(web, 3)
    await link(web, experiment, base, runs)
    ws = web.tenant.context.workspace_id
    async with web.engine.begin() as conn:
        for run, model, agent in zip(runs, ("m1", "m1", "m2"), ("a", "a", "b"), strict=True):
            await conn.execute(
                update(t.runs).where(t.runs.c.workspace_id == ws, t.runs.c.id == run)
                .values(summary={"models": [model]}, agent_slug=agent)
            )  # fmt: skip
    codes = {w["code"] for w in (await detail(web, owner, experiment))["warnings"]}
    assert {"variant_models_vary", "variant_agents_vary"} <= codes


# ------------------------------------------------------------------ evaluate all


async def test_evaluate_inserts_the_missing_pairs_one_job_per_run_and_skips_what_it_must(
    fast: Api,
) -> None:
    web = fast
    owner = await person(web, "o@acme.test", "OWNER")
    runs = [await seeded_run(web.engine, web.tenant, web.worker, project="alpha") for _ in range(3)]
    a = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="a")
    b = await add_evaluator(web.engine, web.tenant, "tests_passed", name="b")
    human = await add_evaluator(web.engine, web.tenant, "human", kind="human", name="rev")
    refs = [from_uuid(IdKind.EVALUATOR, e.id) for e in (a, b, human)]
    experiment = (await make(web, owner, evaluator_ids=refs)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    for run in runs:
        assert (await attach(web, owner, experiment, run, base)).status_code == 201
    url = f"/v1/experiments/{experiment}/evaluate"

    first = await web.client.post(url, headers=owner)
    assert first.status_code == 202, first.text
    out = first.json()
    assert (out["enqueued"], out["remaining"], out["jobs"]) == (6, 0, 3)  # 3 runs x 2 usable
    assert out["skipped_evaluators"] == [{"evaluator_id": refs[2], "reason": "human"}]
    assert len(await jobs_of(web.engine, EVALUATE_RUN)) == 3
    repeat = await web.client.post(url, headers=owner)
    assert repeat.json()["enqueued"] == 0 and len(await jobs_of(web.engine, EVALUATE_RUN)) == 3

    await web.drain()
    states = {r.state for run in runs for r in await evaluation_rows(web.engine, run)}
    assert states == {"completed"}
    done = await web.client.post(url, headers=owner)
    assert done.json()["enqueued"] == 0  # completed pairs are left alone
    body = await detail(web, owner, experiment)
    missing = [w for w in body["warnings"] if w["code"] == "unevaluated_runs"]
    assert [w["count"] for w in missing] == [3]  # the human verdicts nobody has recorded
    assert {m["evaluations"][0]["completed"] for m in body["metrics"]} == {3}

    # A failed pair is retried; an archived evaluator is skipped and reported.
    ws = web.tenant.context.workspace_id
    async with web.engine.begin() as conn:
        await conn.execute(
            insert(t.evaluations).values(
                workspace_id=ws, id=new_uuid(IdKind.EVALUATION), run_id=runs[0], evaluator_id=a.id,
                evaluator_name="a", evaluator_version="1", kind="deterministic", state="failed",
                error_code="EVALUATOR_ERROR", created_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )  # fmt: skip
    await web.client.delete(f"/v1/evaluators/{from_uuid(IdKind.EVALUATOR, b.id)}", headers=owner)
    # runs[0]'s newest row for `a` is now failed but a completed row exists: left alone.
    again = await web.client.post(url, headers=owner)
    reasons = {s["reason"] for s in again.json()["skipped_evaluators"]}
    assert reasons == {"human", "archived"} and again.json()["enqueued"] == 0


async def test_evaluate_caps_each_call_at_five_hundred_new_rows_and_reports_the_rest(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    evaluators = [
        await add_evaluator(web.engine, web.tenant, "run_succeeded", name=f"e{n}") for n in range(3)
    ]
    refs = [from_uuid(IdKind.EVALUATOR, e.id) for e in evaluators]
    experiment = (await make(web, owner, evaluator_ids=refs)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    await link(web, experiment, base, await bulk_runs(web, 200))  # 600 pairs
    url = f"/v1/experiments/{experiment}/evaluate"
    first = (await web.client.post(url, headers=owner)).json()
    assert (first["enqueued"], first["remaining"]) == (500, 100)
    assert 167 <= first["jobs"] <= 200  # one job per run touched
    second = (await web.client.post(url, headers=owner)).json()
    assert (second["enqueued"], second["remaining"]) == (100, 0)
    third = (await web.client.post(url, headers=owner)).json()
    assert (third["enqueued"], third["remaining"]) == (0, 0)  # everything is pending
    async with web.engine.connect() as conn:
        total = (await conn.execute(select(text("count(*)")).select_from(t.evaluations))).scalar()
    assert total == 600
    assert len(await jobs_of(web.engine, EVALUATE_RUN)) == 200
    delayed = [j for j in await jobs_of(web.engine, EVALUATE_RUN)]
    assert all(j.available_at > j.created_at for j in delayed)  # the batch delay


async def test_evaluate_retries_failed_pairs_and_skips_pairs_at_the_per_evaluator_bound(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    full, retry = await bulk_runs(web, 2)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="a")
    ref = from_uuid(IdKind.EVALUATOR, evaluator.id)
    experiment = (await make(web, owner, evaluator_ids=[ref])).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    await link(web, experiment, base, [full, retry])
    ws = web.tenant.context.workspace_id
    async with web.engine.begin() as conn:
        await conn.execute(
            insert(t.evaluations),
            [
                {"workspace_id": ws, "id": new_uuid(IdKind.EVALUATION), "run_id": run,
                 "evaluator_id": evaluator.id, "evaluator_name": "a", "evaluator_version": "1",
                 "kind": "deterministic", "state": "failed", "error_code": "TIMEOUT"}
                for run, count in ((full, 20), (retry, 1))
                for _ in range(count)
            ],
        )  # fmt: skip
    out = (await web.client.post(f"/v1/experiments/{experiment}/evaluate", headers=owner)).json()
    assert (out["enqueued"], out["at_limit"], out["jobs"]) == (1, 1, 1)  # retry one, skip the full


async def test_evaluate_all_skips_a_judge_that_is_cooling_down_instead_of_burning_rows(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        eval_batch_delay_seconds=0,
        judge_provider="fake",
        judge_cooldown_seconds=300,
    )
    async for web in build_api(database_url, engine, settings=settings):
        owner = await person(web, "o@acme.test", "OWNER")
        (run,) = await bulk_runs(web, 1)
        judge = await add_evaluator(
            web.engine, web.tenant, "judge", {"rubric": "r"}, kind="llm_judge", name="j"
        )
        ref = from_uuid(IdKind.EVALUATOR, judge.id)
        experiment = (await make(web, owner, evaluator_ids=[ref])).json()["id"]
        base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
        await link(web, experiment, base, [run])
        async with web.engine.begin() as conn:  # an expensive reply, just now
            await conn.execute(
                insert(t.evaluations).values(
                    workspace_id=web.tenant.context.workspace_id,
                    id=new_uuid(IdKind.EVALUATION), run_id=run, evaluator_id=judge.id,
                    evaluator_name="j", evaluator_version="1", kind="llm_judge",
                    state="failed", error_code="JUDGE_BUDGET", completed_at=text("now()"),
                )
            )  # fmt: skip
        url = f"/v1/experiments/{experiment}/evaluate"
        for _ in range(2):
            out = (await web.client.post(url, headers=owner)).json()
            assert out["enqueued"] == 0
            assert out["skipped_evaluators"] == [{"evaluator_id": ref, "reason": "cooling_down"}]
        assert len(await evaluation_rows(web.engine, run)) == 1


async def test_evaluate_all_and_triggers_race_without_overshooting_the_row_bound(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="a")
    experiment = (
        await make(web, owner, evaluator_ids=[from_uuid(IdKind.EVALUATOR, evaluator.id)])
    ).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    await attach(web, owner, experiment, run, base)
    trigger_url = f"/v1/runs/{from_uuid(IdKind.RUN, run)}/evaluations"
    body = {"evaluator_id": from_uuid(IdKind.EVALUATOR, evaluator.id)}
    results = await asyncio.gather(
        *[
            web.client.post(f"/v1/experiments/{experiment}/evaluate", headers=owner)
            for _ in range(5)
        ],
        *[web.client.post(trigger_url, json=body, headers=owner) for _ in range(5)],
    )
    assert all(r.status_code in (200, 202) for r in results)
    assert len(await evaluation_rows(web.engine, run)) == 1  # one live row, however they raced


# ------------------------------------------------------------------ roles, scope, audit


async def test_roles_project_keys_and_the_audit_trail(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    billing = await person(web, "b@acme.test", "BILLING")
    experiment = (await make(web, owner)).json()["id"]
    base = (await variant(web, owner, experiment, "b", "baseline", m="1")).json()["id"]
    run = await seeded_run(web.engine, web.tenant, web.worker, project="alpha")
    await attach(web, owner, experiment, run, base)
    await web.client.post(f"/v1/experiments/{experiment}/evaluate", headers=owner)
    await web.client.delete(
        f"/v1/experiments/{experiment}/runs/{from_uuid(IdKind.RUN, run)}", headers=owner
    )
    await web.client.delete(f"/v1/experiments/{experiment}", headers=owner)

    assert (await web.client.get("/v1/experiments", headers=viewer)).status_code == 200
    assert (await web.client.get("/v1/experiments", headers=billing)).status_code == 403
    assert (await make(web, viewer)).status_code == 403
    assert (await variant(web, viewer, experiment, "x", "candidate", m="2")).status_code == 403
    assert (
        await web.client.post(f"/v1/experiments/{experiment}/evaluate", headers=viewer)
    ).status_code == 403

    key = web.headers("reader")  # a runs:read key bound to alpha
    del key["content-type"]
    listed = await web.client.get("/v1/experiments?include_archived=true", headers=key)
    assert listed.status_code == 200 and [e["id"] for e in listed.json()["items"]] == [experiment]
    assert (await web.client.get(f"/v1/experiments/{experiment}", headers=key)).status_code == 200
    beta = web.headers("beta")
    del beta["content-type"]
    hidden = await web.client.get(f"/v1/experiments/{experiment}", headers=beta)
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "EXPERIMENT_NOT_FOUND"
    assert (await web.client.get("/v1/experiments", headers=beta)).json()["items"] == []
    denied = await web.client.post(
        "/v1/experiments", json={"name": "x", "project_id": pid(web)}, headers=web.headers("reader")
    )
    assert denied.status_code == 403

    actions_seen = [
        e.action for e in await audit_rows(web)
        if e.action.startswith("experiment.") and e.outcome == "allowed"
    ]  # fmt: skip
    assert sorted(set(actions_seen)) == [
        "experiment.archive", "experiment.create", "experiment.evaluate",
        "experiment.run.attach", "experiment.run.detach", "experiment.variant.create",
    ]  # fmt: skip
    for entry in await audit_rows(web):
        assert "Prompt v2" not in repr(entry)  # names are free text: never in audit details


async def test_foreign_and_random_ids_are_one_404(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    mine = (await make(web, owner)).json()["id"]
    foreign = from_uuid(IdKind.EXPERIMENT, uuid.uuid4())
    bodies = []
    for experiment in (foreign, from_uuid(IdKind.EXPERIMENT, uuid.uuid4())):
        for method, suffix in (("GET", ""), ("DELETE", ""), ("POST", "/evaluate")):
            response = await web.client.request(
                method, f"/v1/experiments/{experiment}{suffix}", headers=owner
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "EXPERIMENT_NOT_FOUND"
            bodies.append({**response.json()["error"], "request_id": None})
    assert all(b == bodies[0] for b in bodies)
    assert (await web.client.get(f"/v1/experiments/{mine}", headers=owner)).status_code == 200


async def test_pair_states_never_read_another_workspaces_experiment(web: Api) -> None:
    """INV-3 below the HTTP layer: the pair read carries the workspace, whatever ids it is given."""
    from abb_api.evaluations.repository import EvaluationRepository
    from abb_api.experiments.repository import ExperimentRepository

    other = web.other
    run = await seeded_run(web.engine, other, web.worker)
    evaluator = await add_evaluator(web.engine, other, "run_succeeded", name="theirs")
    experiment, variant_id = new_uuid(IdKind.EXPERIMENT), new_uuid(IdKind.VARIANT)
    async with web.engine.begin() as conn:
        theirs = ExperimentRepository(conn, other.context)
        await theirs.create(
            experiment, project_id=other.project_uuids["p"], name="x", description=None,
            dataset_label=None, evaluator_ids=[from_uuid(IdKind.EVALUATOR, evaluator.id)],
            created_by=None,
        )  # fmt: skip
        await theirs.create_variant(
            variant_id, experiment_id=experiment, name="b", role="baseline", config={}
        )
        await theirs.attach(experiment, run, variant_id, None)
        await EvaluationRepository(conn, other.context).insert_pending(
            new_uuid(IdKind.EVALUATION), run_id=run, evaluator=evaluator, requested_by=None
        )
    async with web.engine.connect() as conn:
        assert (
            len(
                await ExperimentRepository(conn, other.context).pair_states(
                    experiment, [evaluator.id]
                )
            )
            == 1
        )
        assert (
            await ExperimentRepository(conn, web.tenant.context).pair_states(
                experiment, [evaluator.id]
            )
            == []
        )
