"""Per-project quotas (Phase 19a step 3, ADR-154): refusal at the limit, nothing stored lost.

Real application, real PostgreSQL. Each quota is exercised at exactly its limit, with existing
data, with replays, across projects and workspaces, under concurrency, over the UTC day boundary,
and through the usage and override API with its roles and audit.
"""

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.db import tables as t
from abb_api.projects.repository import ProjectRepository
from abb_api.quotas.model import (
    Candidate,
    Quota,
    QuotaLimits,
    QuotaOverrides,
    admit,
    status_of,
)
from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event

BATCH = "/v1/events/batch"
HUGE = {
    "quota_agents": 1000,
    "quota_runs_per_day": 100_000,
    "quota_events_per_day": 100_000,
    "quota_artifact_bytes": 1024**4,
}

MakeApi = Callable[..., Awaitable[Api]]


@pytest.fixture
async def make_api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[MakeApi]:
    """Builds the usual two-tenant application with the given quota settings (once per test)."""
    generators: list[Any] = []

    async def make(**caps: Any) -> Api:
        settings = make_settings(
            database_url,
            web_origin=WEB_ORIGIN,
            artifact_dir=str(tmp_path / "store"),
            artifact_max_bytes=4096,
            **{**HUGE, **caps},
        )
        generator = build_api(database_url, engine, settings=settings)
        generators.append(generator)
        api = await generator.__anext__()
        async with engine.begin() as conn:
            keys = ApiKeyRepository(conn, api.tenant.context)
            api.tokens["art"] = (
                await keys.create(
                    scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                    project_id=api.tenant.project_uuids["alpha"],
                )
            ).token
            api.tokens["art_beta"] = (
                await keys.create(
                    scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                    project_id=api.tenant.project_uuids["beta"],
                )
            ).token
        return api

    yield make
    for generator in generators:
        await generator.aclose()


def event(run: dict[str, str], n: int, agent: str = "a1", **extra: Any) -> dict[str, Any]:
    return wire_event(run, n, agent_id=agent, **extra)


async def send(api: Api, events: list[Any], token: str = "writer") -> dict[str, Any]:
    response = await api.post_batch(events, token=token)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


def refused(body: dict[str, Any]) -> dict[int, str]:
    """index -> quota for every QUOTA_EXCEEDED error of a batch response."""
    return {e["index"]: e["quota"] for e in body["errors"] if e["code"] == "QUOTA_EXCEEDED"}


async def scalar(engine: AsyncEngine, statement: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(statement)).scalar_one()


async def count(engine: AsyncEngine, table: Any, *where: Any) -> int:
    return int(await scalar(engine, select(func.count()).select_from(table).where(*where)))


async def usage_row(engine: AsyncEngine, project: uuid.UUID) -> tuple[int, int]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    func.coalesce(func.sum(t.project_usage_daily.c.runs_created), 0),
                    func.coalesce(func.sum(t.project_usage_daily.c.events_accepted), 0),
                ).where(t.project_usage_daily.c.project_id == project)
            )
        ).one()
    return int(row[0]), int(row[1])


# ------------------------------------------------------------------ the pure decision

LIMITS = QuotaLimits(agents=2, runs_per_day=2, events_per_day=5, artifact_bytes=100)


def candidates(*rows: tuple[str, int]) -> list[Candidate]:
    runs = {n: uuid.uuid4() for n in {r for _, r in rows}}
    return [Candidate(i, agent, runs[run]) for i, (agent, run) in enumerate(rows)]


def test_admit_accepts_up_to_exactly_each_limit() -> None:
    base: dict[str, Any] = {"known_agents": set(), "known_runs": set(), "agents_used": 0}
    base |= {"runs_today": 0, "events_today": 0}
    # agents: two slugs fit, the third is refused
    got = admit(candidates(("a", 1), ("b", 1), ("c", 1)), limits=LIMITS, **base)
    assert got.accepted == [0, 1] and got.refused == {2: Quota.AGENTS}
    # runs: two runs fit, the third is refused
    got = admit(candidates(("a", 1), ("a", 2), ("a", 3)), limits=LIMITS, **base)
    assert got.accepted == [0, 1] and got.refused == {2: Quota.RUNS_PER_DAY}
    # events: five fit, the sixth is refused
    got = admit(candidates(*[("a", 1)] * 6), limits=LIMITS, **base)
    assert got.accepted == [0, 1, 2, 3, 4] and got.refused == {5: Quota.EVENTS_PER_DAY}


def test_admit_never_refuses_what_already_exists() -> None:
    cs = candidates(("a", 1), ("b", 2))
    known_runs = {cs[0].run, cs[1].run}
    got = admit(
        cs,
        limits=LIMITS,
        agents_used=2,
        runs_today=2,
        events_today=0,
        known_agents={"a", "b"},
        known_runs=known_runs,
    )
    assert got.accepted == [0, 1] and not got.refused and not got.new_agents and not got.new_runs


def test_a_refused_agent_or_run_stays_refused_and_costs_nothing() -> None:
    cs = candidates(("c", 1), ("a", 2), ("c", 1), ("a", 3))
    got = admit(
        cs,
        limits=QuotaLimits(1, 2, 100, 1),
        agents_used=0,
        runs_today=0,
        events_today=0,
        known_agents=set(),
        known_runs=set(),
    )
    # "c" took the only agent slot; "a" is refused (twice: the refusal is remembered), and the
    # runs those refused events would have made were not counted
    assert got.accepted == [0, 2]
    assert got.refused == {1: Quota.AGENTS, 3: Quota.AGENTS}
    assert got.new_agents == {"c"} and len(got.new_runs) == 1


def test_an_event_refused_for_the_day_does_not_count_its_run_or_agent() -> None:
    cs = candidates(("a", 1), ("b", 2))
    got = admit(
        cs,
        limits=LIMITS,
        agents_used=0,
        runs_today=0,
        events_today=5,
        known_agents=set(),
        known_runs=set(),
    )
    assert got.refused == {0: Quota.EVENTS_PER_DAY, 1: Quota.EVENTS_PER_DAY}
    assert not got.new_agents and not got.new_runs


def test_status_words() -> None:
    assert status_of(0, 100) == "ok" and status_of(79, 100) == "ok"
    assert status_of(80, 100) == "near_limit" and status_of(99, 100) == "near_limit"
    assert status_of(100, 100) == "at_limit" and status_of(150, 100) == "at_limit"


def test_overrides_resolve_over_defaults() -> None:
    got = QuotaOverrides(agents=7).resolve(LIMITS)
    assert got == QuotaLimits(agents=7, runs_per_day=2, events_per_day=5, artifact_bytes=100)


# ------------------------------------------------------------------ distinct agents


async def test_a_new_agent_slug_is_refused_at_the_limit_and_nothing_else_is_stored(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_agents=2)
    run1, run2, run3 = make_run_ids(), make_run_ids(), make_run_ids()
    body = await send(api, [event(run1, 1, "a1"), event(run2, 1, "a2"), event(run3, 1, "a3")])
    assert (body["accepted"], body["rejected"]) == (2, 1)
    assert refused(body) == {2: "agents"}
    error = body["errors"][0]
    assert error["issues"][0]["loc"] == ["quota"] and error["issues"][0]["code"] == "agents"
    assert await count(api.engine, t.agents) == 2
    assert await count(api.engine, t.runs) == 2  # the refused event created no run
    assert await count(api.engine, t.events) == 2


async def test_existing_agents_and_runs_keep_working_at_the_agent_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_agents=2)
    run1, run2 = make_run_ids(), make_run_ids()
    await send(api, [event(run1, 1, "a1"), event(run2, 1, "a2")])
    # more events of the existing runs, and a NEW run of an existing agent, are all accepted
    body = await send(
        api, [event(run1, 2, "a1"), event(run2, 2, "a2"), event(make_run_ids(), 1, "a1")]
    )
    assert (body["accepted"], body["rejected"]) == (3, 0)
    # a new slug is refused even inside an existing run
    body = await send(api, [event(run1, 3, "a-new")])
    assert refused(body) == {0: "agents"}


async def test_raising_the_limit_lets_the_refused_agent_in_and_a_replay_is_not_double_counted(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_agents=1)
    run = make_run_ids()
    first = event(run, 1, "a1")
    second = event(make_run_ids(), 1, "a2")
    body = await send(api, [first, second])
    assert refused(body) == {1: "agents"}
    again = await send(api, [first, second])  # the replay: first is a duplicate, second refused
    assert (again["duplicates"], again["accepted"]) == (1, 0)
    assert refused(again) == {1: "agents"}
    assert await count(api.engine, t.events) == 1
    async with api.engine.begin() as conn:
        await conn.execute(
            t.project_quota_overrides.insert().values(
                workspace_id=api.tenant.context.workspace_id,
                project_id=api.tenant.project_uuids["alpha"],
                max_agents=5,
                updated_by="test",
            )
        )
    body = await send(api, [second])
    assert (body["accepted"], body["rejected"]) == (1, 0)


async def test_agent_limit_race_never_passes_the_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_agents=3)
    bodies = await asyncio.gather(
        *[send(api, [event(make_run_ids(), 1, f"racer-{i}")]) for i in range(16)]
    )
    assert sum(b["accepted"] for b in bodies) == 3
    assert await count(api.engine, t.agents) == 3  # not "about three": exactly three


# ------------------------------------------------------------------ runs per day


async def test_a_new_run_is_refused_at_the_daily_limit_but_existing_runs_continue(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_runs_per_day=2)
    r1, r2, r3 = make_run_ids(), make_run_ids(), make_run_ids()
    body = await send(api, [event(r1, 1), event(r2, 1), event(r3, 1), event(r3, 2)])
    assert (body["accepted"], body["rejected"]) == (2, 2)
    assert refused(body) == {2: "runs_per_day", 3: "runs_per_day"}  # every event of the new run
    body = await send(api, [event(r1, 2), event(r2, 2), event(r3, 3)])
    assert refused(body) == {2: "runs_per_day"} and body["accepted"] == 2
    assert await count(api.engine, t.runs) == 2
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (2, 4)


async def test_runs_per_day_starts_again_after_utc_midnight(make_api: MakeApi) -> None:
    api = await make_api(quota_runs_per_day=1, quota_events_per_day=2)
    r1, r2 = make_run_ids(), make_run_ids()
    assert refused(await send(api, [event(r1, 1), event(r2, 1)])) == {1: "runs_per_day"}
    api.clock.now = api.clock.now.replace(hour=23, minute=59, second=59)
    assert refused(await send(api, [event(r2, 1)])) == {0: "runs_per_day"}  # still the same day
    api.clock.now += timedelta(seconds=1)  # 00:00:00 UTC of the next day
    body = await send(api, [event(r2, 1), event(r1, 2)])
    assert (body["accepted"], body["rejected"]) == (2, 0)  # r2 is today's one new run
    assert refused(await send(api, [event(r1, 3)])) == {0: "events_per_day"}  # 2 events today
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (2, 3)


async def test_create_run_counts_and_is_refused_at_the_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_runs_per_day=2)
    headers = api.headers("writer")
    first = await api.client.post("/v1/runs", json={}, headers=headers)
    assert first.status_code == 201, first.text
    run_id = first.json()["id"]
    assert (
        await api.client.post("/v1/runs", json={"run_id": run_id}, headers=headers)
    ).status_code == 200
    # an event for another new run takes the second slot (the same counter)
    assert (await send(api, [event(make_run_ids(), 1)]))["accepted"] == 1
    third = await api.client.post("/v1/runs", json={}, headers=headers)
    assert third.status_code == 413 and third.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert third.json()["error"]["details"]["quota"] == "runs_per_day"
    # an existing run id is still answered
    assert (
        await api.client.post("/v1/runs", json={"run_id": run_id}, headers=headers)
    ).status_code == 200


# ------------------------------------------------------------------ events per day


async def test_events_per_day_refuses_exactly_the_excess_in_batch_order(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=5)
    run = make_run_ids()
    body = await send(api, [event(run, n) for n in range(1, 9)])
    assert (body["accepted"], body["rejected"]) == (5, 3)
    assert refused(body) == {5: "events_per_day", 6: "events_per_day", 7: "events_per_day"}
    assert await count(api.engine, t.events) == 5
    assert refused(await send(api, [event(run, 9)])) == {0: "events_per_day"}


async def test_exactly_the_limit_is_accepted_without_a_refusal(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=4)
    run = make_run_ids()
    body = await send(api, [event(run, n) for n in range(1, 5)])
    assert (body["accepted"], body["rejected"], body["errors"]) == (4, 0, [])


async def test_replays_are_never_refused_and_never_counted_again(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=3)
    run = make_run_ids()
    sent = [event(run, n) for n in range(1, 4)]
    assert (await send(api, sent))["accepted"] == 3  # the day is now full
    for _ in range(3):
        again = await send(api, sent)
        assert (again["duplicates"], again["accepted"], again["rejected"]) == (3, 0, 0)
    mixed = await send(api, [sent[0], event(run, 4), sent[2]])
    assert mixed["duplicates"] == 2 and refused(mixed) == {1: "events_per_day"}
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (1, 3)


async def test_a_conflicting_replay_is_a_conflict_not_a_quota_refusal(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=1)
    run = make_run_ids()
    original = event(run, 1)
    await send(api, [original])
    changed = {**original, "attributes": {"tool.name": "different"}}
    body = await send(api, [changed])
    assert body["conflicts"] == 1 and not refused(body)


async def test_events_per_day_race_never_passes_the_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=25)
    runs = [make_run_ids() for _ in range(10)]
    bodies = await asyncio.gather(
        *[send(api, [event(run, n) for n in range(1, 6)]) for run in runs]
    )
    assert sum(b["accepted"] for b in bodies) == 25
    assert await count(api.engine, t.events) == 25
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (5, 25)


async def test_runs_per_day_race_never_passes_the_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_runs_per_day=4)
    bodies = await asyncio.gather(*[send(api, [event(make_run_ids(), 1)]) for _ in range(12)])
    assert sum(b["accepted"] for b in bodies) == 4
    assert await count(api.engine, t.runs) == 4


async def test_concurrent_replays_of_the_same_batch_count_once(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=6)
    run = make_run_ids()
    same = [event(run, n) for n in range(1, 4)]
    bodies = await asyncio.gather(*[send(api, same) for _ in range(8)])
    assert sum(b["accepted"] for b in bodies) == 3
    assert all(not refused(b) for b in bodies)
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (1, 3)
    assert (await send(api, [event(make_run_ids(), n) for n in range(1, 4)]))["accepted"] == 3


# ------------------------------------------------------------------ isolation


async def test_one_projects_usage_never_counts_against_another(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=2, quota_agents=1, quota_runs_per_day=1)
    run = make_run_ids()
    assert refused(await send(api, [event(run, n) for n in range(1, 4)])) == {2: "events_per_day"}
    # beta (same workspace) and globex (another workspace) each have their own full allowance
    body = await send(api, [event(make_run_ids(), 1, "b1"), event(make_run_ids(), 1, "b1")], "beta")
    assert (body["accepted"], refused(body)) == (1, {1: "runs_per_day"})
    body = await send(
        api, [event(make_run_ids(), 1, "g1"), event(make_run_ids(), 2, "g1")], "other"
    )
    assert (body["accepted"], refused(body)) == (1, {1: "runs_per_day"})
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (1, 2)
    assert await usage_row(api.engine, api.tenant.project_uuids["beta"]) == (1, 1)
    assert await usage_row(api.engine, api.other.project_uuids["p"]) == (1, 1)


async def test_the_same_slug_in_two_projects_counts_in_each(make_api: MakeApi) -> None:
    api = await make_api(quota_agents=1)
    assert (await send(api, [event(make_run_ids(), 1, "same")]))["accepted"] == 1
    assert (await send(api, [event(make_run_ids(), 1, "same")], "beta"))["accepted"] == 1
    assert refused(await send(api, [event(make_run_ids(), 1, "other")], "beta")) == {0: "agents"}


async def test_a_run_of_another_project_is_a_mismatch_not_a_quota_decision(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_runs_per_day=1, quota_events_per_day=1)
    run = make_run_ids()
    await send(api, [event(run, 1)])
    body = await send(api, [event(run, 2)], "beta")
    assert body["errors"][0]["code"] == "RUN_PROJECT_MISMATCH"
    assert await usage_row(api.engine, api.tenant.project_uuids["beta"]) == (0, 0)


# ------------------------------------------------------------------ the project lock


async def test_a_batch_waits_a_bounded_time_for_a_held_project_lock(make_api: MakeApi) -> None:
    api = await make_api(quota_lock_timeout_seconds=0.3)
    alpha = api.tenant.project_uuids["alpha"]
    async with api.engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(
            select(t.projects.c.id).where(t.projects.c.id == alpha).with_for_update(key_share=True)
        )
        started = time.monotonic()
        batch = await api.post_batch([event(make_run_ids(), 1)])
        assert time.monotonic() - started < 5
        assert batch.status_code == 503, batch.text
        assert batch.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert batch.json()["error"]["retryable"] is True
        created = await api.client.post("/v1/runs", json={}, headers=api.headers("writer"))
        assert created.status_code == 503, created.text
        # nothing was stored, and another project is not waiting for this one
        assert await count(api.engine, t.events) == 0 and await count(api.engine, t.runs) == 0
        other = await send(api, [event(make_run_ids(), 1)], "beta")
        assert other["accepted"] == 1
        await transaction.rollback()
    assert (await send(api, [event(make_run_ids(), 1)]))["accepted"] == 1  # a retry then succeeds


# ------------------------------------------------------------------ the single-event endpoint


async def test_the_single_event_endpoint_answers_413_with_the_code_and_the_quota(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_events_per_day=1)
    run = make_run_ids()
    first = event(run, 1)
    ok = await api.client.post("/v1/events", json=first, headers=api.headers("writer"))
    assert ok.status_code == 202
    over = await api.client.post("/v1/events", json=event(run, 2), headers=api.headers("writer"))
    assert over.status_code == 413
    error = over.json()["error"]
    assert error["code"] == "QUOTA_EXCEEDED" and error["details"] == {"quota": "events_per_day"}
    assert "retry-after" not in over.headers and error["retryable"] is False
    replay = await api.client.post("/v1/events", json=first, headers=api.headers("writer"))
    assert replay.status_code == 202 and replay.json()["status"] == "duplicate"


# ------------------------------------------------------------------ switches


async def test_quotas_off_checks_and_counts_nothing(make_api: MakeApi) -> None:
    api = await make_api(quotas_enabled=False, quota_events_per_day=1, quota_agents=1)
    body = await send(api, [event(make_run_ids(), n, f"a{n}") for n in range(1, 5)])
    assert (body["accepted"], body["rejected"]) == (4, 0)
    assert await usage_row(api.engine, api.tenant.project_uuids["alpha"]) == (0, 0)


# ------------------------------------------------------------------ artifacts

RUN = new_id(IdKind.RUN)


async def put_artifact(
    api: Api, body: bytes, *, token: str = "art", artifact_id: str | None = None
) -> httpx.Response:
    aid = artifact_id or new_id(IdKind.ARTIFACT)
    return await api.client.put(
        f"/v1/artifacts/{aid}",
        content=body,
        params={"run_id": RUN, "kind": "stdout"},
        headers={
            "content-type": "text/plain",
            "authorization": f"Bearer {api.tokens[token]}",
        },
    )


async def artifact_bytes(engine: AsyncEngine, project: uuid.UUID) -> int:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(t.project_artifact_usage.c.bytes_stored).where(
                    t.project_artifact_usage.c.project_id == project
                )
            )
        ).first()
    return int(row[0]) if row else 0


async def test_artifact_bytes_are_refused_at_the_limit_with_413(
    make_api: MakeApi, tmp_path: Path
) -> None:
    api = await make_api(quota_artifact_bytes=2500)
    alpha = api.tenant.project_uuids["alpha"]
    assert (await put_artifact(api, b"a" * 1000)).status_code == 201
    assert (await put_artifact(api, b"b" * 1000)).status_code == 201
    over = await put_artifact(api, b"c" * 1000)  # 3000 > 2500
    assert over.status_code == 413
    error = over.json()["error"]
    assert error["code"] == "QUOTA_EXCEEDED" and error["details"]["quota"] == "artifact_bytes"
    assert error["details"]["limit"] == 2500 and error["details"]["used"] == 2000
    assert await artifact_bytes(api.engine, alpha) == 2000
    assert await count(api.engine, t.artifacts) == 2
    assert len([p for p in (tmp_path / "store").rglob("*") if p.is_file()]) == 2
    # a smaller one that fits is still accepted: exactly up to the limit
    assert (await put_artifact(api, b"d" * 500)).status_code == 201
    assert await artifact_bytes(api.engine, alpha) == 2500
    assert (await put_artifact(api, b"e")).status_code == 413


async def test_an_artifact_retry_is_a_duplicate_even_at_the_limit(make_api: MakeApi) -> None:
    api = await make_api(quota_artifact_bytes=1024)
    aid, body = new_id(IdKind.ARTIFACT), b"x" * 1024
    assert (await put_artifact(api, body, artifact_id=aid)).status_code == 201
    again = await put_artifact(api, body, artifact_id=aid)
    assert again.status_code == 200
    assert await artifact_bytes(api.engine, api.tenant.project_uuids["alpha"]) == 1024


async def test_concurrent_artifact_uploads_cannot_pass_the_limit(
    make_api: MakeApi, tmp_path: Path
) -> None:
    api = await make_api(quota_artifact_bytes=2500)
    alpha = api.tenant.project_uuids["alpha"]
    responses = await asyncio.gather(*[put_artifact(api, bytes([65 + i]) * 1000) for i in range(8)])
    ok = [r for r in responses if r.status_code == 201]
    assert len(ok) == 2 and all(r.status_code == 413 for r in responses if r not in ok)
    assert await artifact_bytes(api.engine, alpha) == 2000
    assert await count(api.engine, t.artifacts) == 2
    # the files of the refused uploads were removed again
    assert len([p for p in (tmp_path / "store").rglob("*") if p.is_file()]) == 2


async def test_concurrent_uploads_of_one_artifact_id_count_once(make_api: MakeApi) -> None:
    api = await make_api(quota_artifact_bytes=1500)
    aid = new_id(IdKind.ARTIFACT)
    responses = await asyncio.gather(
        *[put_artifact(api, b"z" * 1000, artifact_id=aid) for _ in range(6)]
    )
    assert all(r.status_code in (200, 201) for r in responses)
    assert await artifact_bytes(api.engine, api.tenant.project_uuids["alpha"]) == 1000


async def test_artifact_usage_is_per_project(make_api: MakeApi) -> None:
    api = await make_api(quota_artifact_bytes=1200)
    assert (await put_artifact(api, b"a" * 1000)).status_code == 201
    assert (await put_artifact(api, b"a" * 1000, token="art_beta")).status_code == 201
    assert (await put_artifact(api, b"b" * 1000)).status_code == 413
    assert await artifact_bytes(api.engine, api.tenant.project_uuids["beta"]) == 1000


async def test_artifact_quota_counts_the_stored_form(make_api: MakeApi) -> None:
    api = await make_api(quota_artifact_bytes=1024)
    body = b"y" * 100
    r = await put_artifact(api, body)
    assert r.status_code == 201
    assert r.json()["size_bytes"] == len(body)
    assert r.json()["sha256"] == hashlib.sha256(body).hexdigest()
    assert await artifact_bytes(api.engine, api.tenant.project_uuids["alpha"]) == 100


# ------------------------------------------------------------------ usage API


def row(body: dict[str, Any], quota: str) -> dict[str, Any]:
    return next(q for q in body["quotas"] if q["quota"] == quota)


async def test_usage_matches_a_hand_count(make_api: MakeApi) -> None:
    api = await make_api(
        quota_agents=10, quota_runs_per_day=5, quota_events_per_day=20, quota_artifact_bytes=4096
    )
    run1, run2 = make_run_ids(), make_run_ids()
    await send(api, [event(run1, 1, "a1"), event(run1, 2, "a1"), event(run2, 1, "a2")])
    await put_artifact(api, b"q" * 1000)
    headers = await person(api, "owner@acme.test", "OWNER")
    project = api.tenant.projects["alpha"]
    response = await api.client.get(f"/v1/projects/{project}/quotas", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["day"] == "2026-10-07" and body["resets_at"] == "2026-10-08T00:00:00Z"
    assert body["enforced"] is True and body["project_id"] == project
    assert [q["quota"] for q in body["quotas"]] == [
        "agents",
        "runs_per_day",
        "events_per_day",
        "artifact_bytes",
    ]
    assert row(body, "agents") | {} == {
        "quota": "agents",
        "period": "total",
        "limit": 10,
        "default": 10,
        "overridden": False,
        "used": 2,
        "percent": 20,
        "status": "ok",
    }
    assert (row(body, "runs_per_day")["used"], row(body, "runs_per_day")["percent"]) == (2, 40)
    assert (row(body, "events_per_day")["used"], row(body, "events_per_day")["percent"]) == (3, 15)
    assert (row(body, "artifact_bytes")["used"], row(body, "artifact_bytes")["percent"]) == (
        1000,
        24,
    )
    beta = await api.client.get(
        f"/v1/projects/{api.tenant.projects['beta']}/quotas", headers=headers
    )
    assert [q["used"] for q in beta.json()["quotas"]] == [0, 0, 0, 0]


async def test_usage_shows_near_limit_and_at_limit_in_words(make_api: MakeApi) -> None:
    api = await make_api(quota_events_per_day=5)
    run = make_run_ids()
    await send(api, [event(run, n) for n in range(1, 5)])
    headers = await person(api, "viewer@acme.test", "VIEWER")
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    assert (
        row((await api.client.get(path, headers=headers)).json(), "events_per_day")["status"]
        == "near_limit"
    )
    await send(api, [event(run, 5), event(run, 6)])
    assert (
        row((await api.client.get(path, headers=headers)).json(), "events_per_day")["status"]
        == "at_limit"
    )


async def test_usage_is_visible_to_every_member_and_to_keys_of_the_project_only(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    alpha, beta = api.tenant.projects["alpha"], api.tenant.projects["beta"]
    for role in ("OWNER", "ADMIN", "DEVELOPER", "VIEWER"):
        headers = await person(api, f"{role.lower()}@acme.test", role)
        assert (
            await api.client.get(f"/v1/projects/{alpha}/quotas", headers=headers)
        ).status_code == 200
    assert (await api.get(f"/v1/projects/{alpha}/quotas", token="reader")).status_code == 200
    assert (await api.get(f"/v1/projects/{beta}/quotas", token="reader")).status_code == 404
    assert (await api.get(f"/v1/projects/{beta}/quotas", token="wide_reader")).status_code == 200
    assert (await api.get(f"/v1/projects/{alpha}/quotas", token=None)).status_code == 401
    # another workspace's project is not found, however it is asked
    foreign = api.other.projects["p"]
    owner = await person(api, "owner2@acme.test", "OWNER")
    assert (
        await api.client.get(f"/v1/projects/{foreign}/quotas", headers=owner)
    ).status_code == 404
    assert (await api.client.get("/v1/projects/prj_nope/quotas", headers=owner)).status_code == 404


# ------------------------------------------------------------------ overrides


async def audit_rows(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.audit_log)
            .where(t.audit_log.c.action == "project.quota.update")
            .order_by(t.audit_log.c.id)
        )
        return list(rows)


async def test_owner_and_admin_override_and_it_applies_at_once_and_is_audited(
    make_api: MakeApi,
) -> None:
    api = await make_api(quota_events_per_day=2)
    run = make_run_ids()
    assert refused(await send(api, [event(run, n) for n in range(1, 4)])) == {2: "events_per_day"}
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    owner = await person(api, "owner@acme.test", "OWNER")
    done = await api.client.put(path, json={"events_per_day": 10}, headers=owner)
    assert done.status_code == 200, done.text
    got = row(done.json(), "events_per_day")
    assert (got["limit"], got["default"], got["overridden"]) == (10, 2, True)
    assert done.json()["updated_by"] and done.json()["updated_at"]
    assert (await send(api, [event(run, 3)]))["accepted"] == 1  # applies to the next batch
    admin = await person(api, "admin@acme.test", "ADMIN")
    cleared = await api.client.put(path, json={"events_per_day": None, "agents": 3}, headers=admin)
    assert cleared.status_code == 200
    assert row(cleared.json(), "events_per_day")["overridden"] is False
    assert row(cleared.json(), "events_per_day")["limit"] == 2
    assert row(cleared.json(), "agents")["limit"] == 3
    rows = await audit_rows(api.engine)
    assert [r.details for r in rows] == [
        {"quota": "events_per_day", "old": None, "new": 10},
        {"quota": "agents", "old": None, "new": 3},  # one row per quota, in the closed order
        {"quota": "events_per_day", "old": 10, "new": None},
    ]
    assert all(r.outcome == "allowed" and r.resource_kind == "project" for r in rows)
    assert {r.actor_id for r in rows} == {done.json()["updated_by"], cleared.json()["updated_by"]}


async def test_an_unchanged_request_writes_no_audit_row(make_api: MakeApi) -> None:
    api = await make_api()
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    owner = await person(api, "owner@acme.test", "OWNER")
    assert (await api.client.put(path, json={}, headers=owner)).status_code == 200
    assert (await api.client.put(path, json={"agents": None}, headers=owner)).status_code == 200
    await api.client.put(path, json={"agents": 9}, headers=owner)
    await api.client.put(path, json={"agents": 9}, headers=owner)
    assert len(await audit_rows(api.engine)) == 1


@pytest.mark.parametrize("role", ["DEVELOPER", "VIEWER"])
async def test_other_roles_and_keys_cannot_override(make_api: MakeApi, role: str) -> None:
    api = await make_api()
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    headers = await person(api, f"{role.lower()}@acme.test", role)
    assert (await api.client.put(path, json={"agents": 9}, headers=headers)).status_code == 403
    for token in ("writer", "wide", "reader"):
        denied = await api.client.put(path, json={"agents": 9}, headers=api.headers(token))
        assert denied.status_code == 403
    assert await audit_rows(api.engine) == []
    assert await count(api.engine, t.project_quota_overrides) == 0


async def test_override_of_another_workspaces_project_is_404_and_changes_nothing(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    foreign = api.other.projects["p"]
    response = await api.client.put(
        f"/v1/projects/{foreign}/quotas", json={"agents": 9}, headers=owner
    )
    assert response.status_code == 404
    assert await count(api.engine, t.project_quota_overrides) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"agents": 0},
        {"agents": -1},
        {"agents": 1_000_001},
        {"runs_per_day": 0},
        {"events_per_day": 100_000_000_001},
        {"artifact_bytes": 0},
        {"agents": 1.5},
        {"agents": "10"},
        {"agents": True},
        {"unknown_quota": 5},
    ],
)
async def test_invalid_overrides_are_422_and_change_nothing(
    make_api: MakeApi, body: dict[str, Any]
) -> None:
    api = await make_api()
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    owner = await person(api, "owner@acme.test", "OWNER")
    response = await api.client.put(path, json=body, headers=owner)
    assert response.status_code == 422, response.text
    assert await count(api.engine, t.project_quota_overrides) == 0
    assert await audit_rows(api.engine) == []


async def test_lowering_a_limit_below_use_refuses_new_items_only(make_api: MakeApi) -> None:
    api = await make_api()
    run1, run2 = make_run_ids(), make_run_ids()
    await send(api, [event(run1, 1, "a1"), event(run2, 1, "a2")])
    path = f"/v1/projects/{api.tenant.projects['alpha']}/quotas"
    owner = await person(api, "owner@acme.test", "OWNER")
    lowered = await api.client.put(path, json={"agents": 1}, headers=owner)
    assert lowered.status_code == 200
    got = row(lowered.json(), "agents")
    assert (got["used"], got["limit"], got["percent"], got["status"]) == (2, 1, 200, "at_limit")
    body = await send(api, [event(run1, 2, "a1"), event(run2, 2, "a2"), event(run1, 3, "a3")])
    assert (body["accepted"], refused(body)) == (2, {2: "agents"})
    assert await count(api.engine, t.agents) == 2  # nothing already stored was touched


async def test_deleting_a_project_removes_its_quota_rows(make_api: MakeApi) -> None:
    api = await make_api()
    async with api.engine.begin() as conn:
        gamma = await ProjectRepository(conn, api.tenant.context).create(name="gamma", slug="gamma")
        for table, values in (
            (t.project_quota_overrides, {"max_agents": 3, "updated_by": "test"}),
            (t.project_usage_daily, {"day": api.clock().date()}),
            (t.project_artifact_usage, {"bytes_stored": 5}),
        ):
            await conn.execute(
                table.insert().values(
                    workspace_id=api.tenant.context.workspace_id, project_id=gamma.id, **values
                )
            )
        await conn.execute(t.projects.delete().where(t.projects.c.id == gamma.id))
    for table in (t.project_quota_overrides, t.project_usage_daily, t.project_artifact_usage):
        assert await count(api.engine, table) == 0
