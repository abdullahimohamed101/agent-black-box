"""The evaluation tables enforce their own invariants (ADR-090): INV-1, byte limits, tenancy."""

from typing import Any

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.db import tables as t
from tests.conftest import runtime_url
from tests.factories import make_project, make_run, make_workspace, uid


async def _seed(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        project = await make_project(conn, ws)
        run = await make_run(conn, ws, project)
        evaluator = uid()
        await conn.execute(
            insert(t.evaluators).values(
                workspace_id=ws,
                id=evaluator,
                project_id=project,
                name="tests-passed",
                kind="deterministic",
                implementation="tests_passed",
                version=1,
            )
        )
    return {"ws": ws, "project": project, "run": run, "evaluator": evaluator}


def _evaluation(seed: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "workspace_id": seed["ws"],
        "id": uid(),
        "run_id": seed["run"],
        "evaluator_id": seed["evaluator"],
        "evaluator_name": "tests-passed",
        "evaluator_version": "1",
        "kind": "deterministic",
    }
    values.update(overrides)
    return values


async def test_an_evaluation_starts_pending_with_empty_results(engine: AsyncEngine) -> None:
    seed = await _seed(engine)
    async with engine.begin() as conn:
        await conn.execute(insert(t.evaluations).values(**_evaluation(seed)))
        row = (await conn.execute(select(t.evaluations))).one()
    assert (row.state, row.metrics, row.evidence_event_ids, row.config_snapshot) == (
        "pending",
        {},
        [],
        {},
    )


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_a_terminal_evaluation_cannot_be_updated_but_a_pending_one_can(
    engine: AsyncEngine, terminal: str
) -> None:
    seed = await _seed(engine)
    values = _evaluation(seed)
    async with engine.begin() as conn:
        await conn.execute(insert(t.evaluations).values(**values))
        await conn.execute(
            update(t.evaluations)
            .where(t.evaluations.c.id == values["id"], t.evaluations.c.state == "pending")
            .values(state=terminal, score=0.5)
        )
    for change in ({"score": 1.0}, {"state": "pending"}, {"label": "tampered"}):
        with pytest.raises(DBAPIError, match="immutable"):
            async with engine.begin() as conn:
                await conn.execute(
                    update(t.evaluations).where(t.evaluations.c.id == values["id"]).values(**change)
                )
    async with engine.begin() as conn:
        row = (await conn.execute(select(t.evaluations))).one()
    assert (row.state, row.score, row.label) == (terminal, 0.5, None)


async def test_the_runtime_role_cannot_rewrite_a_terminal_evaluation_either(
    engine: AsyncEngine, runtime_database_url: str, database_url: str
) -> None:
    seed = await _seed(engine)
    values = _evaluation(seed, state="completed")
    async with engine.begin() as conn:
        await conn.execute(insert(t.evaluations).values(**values))
    runtime = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError, match="immutable"):
            async with runtime.begin() as conn:
                await conn.execute(text("UPDATE evaluations SET score = 1"))
    finally:
        await runtime.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"score": 1.5},
        {"score": -0.1},
        {"state": "done"},
        {"kind": "oracle"},
        {"explanation": "x" * 16385},
        {"explanation": "é" * 8193},  # 16,386 bytes in 8,193 characters
        {"metrics": {"k": "x" * 17000}},
        {"config_snapshot": {"k": "x" * 8200}},
        {"evidence_event_ids": {"not": "a list"}},
        {"label": "x" * 65},
        {"error": "x" * 513},
    ],
)
async def test_evaluation_check_constraints_are_a_backstop(
    engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    seed = await _seed(engine)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.evaluations).values(**_evaluation(seed, **overrides)))


async def test_byte_limits_count_bytes_not_characters(engine: AsyncEngine) -> None:
    seed = await _seed(engine)
    async with engine.begin() as conn:
        # 4,000 characters of a 4-byte script is 16,000 bytes: inside the 16,384-byte limit.
        await conn.execute(
            insert(t.evaluations).values(**_evaluation(seed, explanation="\U0001d4d0" * 4000))
        )


async def test_an_evaluation_cannot_use_another_tenants_evaluator_or_run(
    engine: AsyncEngine,
) -> None:
    a, b = await _seed(engine), await _seed(engine)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.evaluations).values(**_evaluation(a, evaluator_id=b["evaluator"]))
            )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.evaluations).values(**_evaluation(a, run_id=b["run"])))


async def test_an_evaluator_with_results_cannot_be_deleted_and_a_run_takes_its_results(
    engine: AsyncEngine,
) -> None:
    seed = await _seed(engine)
    async with engine.begin() as conn:
        await conn.execute(insert(t.evaluations).values(**_evaluation(seed)))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM evaluators"))
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM runs"))  # ON DELETE CASCADE
        assert (await conn.execute(text("SELECT count(*) FROM evaluations"))).scalar_one() == 0


async def test_evaluator_names_are_unique_per_workspace_and_config_is_bounded(
    engine: AsyncEngine,
) -> None:
    seed = await _seed(engine)
    base = {
        "workspace_id": seed["ws"],
        "kind": "metric",
        "implementation": "cost_under",
        "version": 1,
    }
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.evaluators).values(id=uid(), name="tests-passed", **base))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.evaluators).values(id=uid(), name="big", config={"k": "x" * 8200}, **base)
            )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.evaluators).values(id=uid(), name="list", config=[], **base)
            )
    async with engine.begin() as conn:  # the same name in another workspace is fine
        other = await make_workspace(conn)
        await conn.execute(
            insert(t.evaluators).values(
                id=uid(), name="tests-passed", **{**base, "workspace_id": other}
            )
        )


async def _experiment(conn: Any, seed: dict[str, Any]) -> Any:
    experiment = uid()
    await conn.execute(
        insert(t.experiments).values(
            workspace_id=seed["ws"], id=experiment, project_id=seed["project"], name="e"
        )
    )
    return experiment


async def test_an_experiment_has_at_most_one_baseline_and_unique_variant_names(
    engine: AsyncEngine,
) -> None:
    seed = await _seed(engine)
    async with engine.begin() as conn:
        experiment = await _experiment(conn, seed)
        variant = {"workspace_id": seed["ws"], "experiment_id": experiment}
        await conn.execute(
            insert(t.experiment_variants).values(id=uid(), name="a", role="baseline", **variant)
        )
        await conn.execute(
            insert(t.experiment_variants).values(id=uid(), name="b", role="candidate", **variant)
        )
        other = await _experiment(conn, seed)  # another experiment may have its own baseline
        await conn.execute(
            insert(t.experiment_variants).values(
                id=uid(), name="a", role="baseline", workspace_id=seed["ws"], experiment_id=other
            )
        )
    for name, role in (("c", "baseline"), ("b", "candidate"), ("d", "champion")):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    insert(t.experiment_variants).values(id=uid(), name=name, role=role, **variant)
                )


async def test_experiment_runs_are_tenant_scoped_once_per_experiment_and_cascade(
    engine: AsyncEngine,
) -> None:
    a, b = await _seed(engine), await _seed(engine)
    async with engine.begin() as conn:
        experiment = await _experiment(conn, a)
        variant = uid()
        await conn.execute(
            insert(t.experiment_variants).values(
                workspace_id=a["ws"],
                id=variant,
                experiment_id=experiment,
                name="a",
                role="baseline",
            )
        )
        link = {
            "workspace_id": a["ws"],
            "experiment_id": experiment,
            "variant_id": variant,
            "run_id": a["run"],
        }
        await conn.execute(insert(t.experiment_runs).values(**link))
    with pytest.raises(IntegrityError):  # once per experiment
        async with engine.begin() as conn:
            await conn.execute(insert(t.experiment_runs).values(**link))
    with pytest.raises(IntegrityError):  # another tenant's run
        async with engine.begin() as conn:
            await conn.execute(insert(t.experiment_runs).values(**{**link, "run_id": b["run"]}))
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM runs WHERE id = :r"), {"r": a["run"]})
        assert (await conn.execute(text("SELECT count(*) FROM experiment_runs"))).scalar_one() == 0
        await conn.execute(text("DELETE FROM experiments"))
        assert (
            await conn.execute(text("SELECT count(*) FROM experiment_variants"))
        ).scalar_one() == 0
