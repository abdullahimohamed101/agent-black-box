"""What alert evaluation costs the ingestion path (Phase 17 gate 8, ADR-150).

Ingestion (`POST /v1/events/batch`) must not change at all: the producer hooks live in the worker's
summarise and findings jobs and in the approval service, and each is one small INSERT. The
deterministic assertions below are the gate; the timings are printed (run with `-s`) for the
benchmark note and only loosely bounded, because wall-clock assertions on a shared runner flake.
"""

import statistics
import time

from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text

from abb_api.alerts import model as m
from tests.alerts_helpers import rows, run_events, seed_destination, seed_rule
from tests.api_fixtures import Api
from tests.ingest_helpers import wire_event

RUNS = 30
EVENTS_PER_RUN = 100


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def batches(api: Api) -> tuple[list[float], list[float]]:
    """POST latency (ms) of RUNS failing runs of about EVENTS_PER_RUN events each, and the time
    (ms) the worker needs to process everything those posts queued, one run at a time."""
    posts: list[float] = []
    drains: list[float] = []
    for _ in range(RUNS):
        _, events = run_events("FAILED")
        ids = {"run_id": events[0]["run_id"], "trace_id": events[0]["trace_id"]}
        padding = [
            wire_event(ids, n, event_id=new_id(IdKind.EVENT), attributes={"tool.name": "t"})
            for n in range(2, EVENTS_PER_RUN)
        ]
        last = events[-1]
        last["sequence"] = EVENTS_PER_RUN + 1
        batch = [events[0], *padding, last]
        started = time.perf_counter()
        response = await api.post_batch(batch)
        posts.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 202
        started = time.perf_counter()
        await api.drain()
        drains.append((time.perf_counter() - started) * 1000)
    return posts, drains


async def test_alert_rules_add_nothing_to_ingestion_and_one_insert_to_a_failed_run(
    api: Api,
) -> None:
    plain_posts, plain_drains = await batches(api)
    async with api.engine.connect() as conn:
        assert not (await conn.execute(text("SELECT 1 FROM alert_notifications"))).first()
        jobs_without = (
            await conn.execute(
                text("SELECT count(*) FROM outbox_jobs WHERE job_type = 'evaluate_alerts'")
            )
        ).scalar_one()
    assert jobs_without == RUNS  # the hook enqueued one small job per failed run; nothing matched

    destinations = [
        await seed_destination(
            api.engine, api.tenant.context, name=f"d{i}", url=f"https://e{i}.example.org/h"
        )
        for i in range(2)
    ]
    for n, trigger, filters in (
        (0, m.RUN_FAILED, {}),
        (1, m.RUN_FAILED, {"run_statuses": ["FAILED"]}),
        (2, m.SECURITY_FINDING, {"min_severity": "info"}),
        (3, m.APPROVAL_REQUESTED, {}),
        (4, m.APPROVAL_EXPIRING, {"expiring_lead_seconds": 600}),
        (5, m.SPEND_OVER, {"threshold_usd": 5.0, "period": "day"}),
    ):
        await seed_rule(
            api.engine,
            api.tenant.context,
            destinations,
            name=f"r{n}",
            trigger=trigger,
            filters=filters,
        )
    rule_posts, rule_drains = await batches(api)
    found = await rows(api)
    assert len(found) == RUNS * 2 * 2  # two run_failed rules x two destinations x every failed run

    def summary(label: str, values: list[float]) -> str:
        return (
            f"{label}: p50 {statistics.median(values):.1f} ms, "
            f"p95 {percentile(values, 0.95):.1f} ms (n={len(values)})"
        )

    print()
    print(summary(f"ingestion batch of {EVENTS_PER_RUN + 1} events, no rules", plain_posts))
    print(summary(f"ingestion batch of {EVENTS_PER_RUN + 1} events, 6 enabled rules", rule_posts))
    print(summary("worker time per run, no rules", plain_drains))
    print(summary("worker time per run, 6 enabled rules", rule_drains))
    # Loose bounds only; the numbers above are the evidence.
    assert statistics.median(rule_posts) < statistics.median(plain_posts) * 2 + 25
    assert statistics.median(rule_drains) < statistics.median(plain_drains) * 2 + 100
