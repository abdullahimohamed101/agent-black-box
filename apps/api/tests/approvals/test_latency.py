"""Decision latency and its effect on ingestion (gate 8). Numbers are printed (run with -s) and
recorded in docs/benchmarks/phase-14-decide.md; the asserts are wide so a loaded CI runner passes
and an accidental per-call database scan or a blocked event loop does not."""

import asyncio
import statistics
import time

from tests.api_fixtures import Api
from tests.approvals.helpers import decide, intent, publish
from tests.ingest_helpers import make_run_ids, wire_event

RULES = 60
DOC = "version: 1\npolicies:\n" + "".join(
    f"  - id: rule-{i}\n    description: Rule {i}.\n"
    f"    when: {{capability: tool.t{i}, resource: 'path/{i}/**'}}\n    decision: deny\n"
    for i in range(RULES)
)
CALLS = 1000


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


async def timed(coro_factory) -> list[float]:  # type: ignore[no-untyped-def]
    out = []
    for i in range(CALLS):
        t0 = time.perf_counter()
        response = await coro_factory(i)
        out.append((time.perf_counter() - t0) * 1000)
        assert response.status_code == 200
    return out


async def test_decide_latency_p50_p95_over_a_thousand_calls(api: Api) -> None:
    await publish(api, DOC)
    # A miss through 60 rules, the policy's default allow: the common case.
    miss = await timed(lambda i: decide(api, intent(capability="tool.other", resource=f"r{i}")))
    # A hit on the last rule: the whole list is walked, and a deny event is written.
    last = await timed(
        lambda i: decide(
            api, intent(capability=f"tool.t{RULES - 1}", resource=f"path/{RULES - 1}/f{i}")
        )
    )

    def line(label: str, values: list[float]) -> str:
        return (
            f"  {label}  p50 {statistics.median(values):.2f} ms  "
            f"p95 {percentile(values, 0.95):.2f} ms  p99 {percentile(values, 0.99):.2f} ms"
        )

    print(
        f"\ndecide, {RULES} rules, {CALLS} calls each (in-process ASGI, one connection pool):\n"
        + line("allow (miss)    ", miss)
        + "\n"
        + line("deny (last rule)", last)
    )
    assert percentile(miss, 0.95) < 100 and percentile(last, 0.95) < 100


async def batch_times(api: Api, batches: int = 40, size: int = 100) -> list[float]:
    times = []
    for _ in range(batches):
        ids = make_run_ids()
        events = [wire_event(ids, n) for n in range(1, size + 1)]
        t0 = time.perf_counter()
        response = await api.post_batch(events)
        times.append((time.perf_counter() - t0) * 1000)
        assert response.status_code == 202
    return times


async def test_ingestion_latency_is_not_hurt_by_a_stream_of_decisions(api: Api) -> None:
    await publish(api, DOC)
    quiet = await batch_times(api)
    stop = asyncio.Event()

    async def flood() -> int:
        sent = 0
        while not stop.is_set():
            await decide(api, intent(capability="tool.other", resource=str(sent)))
            sent += 1
        return sent

    background = [asyncio.create_task(flood()) for _ in range(4)]
    busy = await batch_times(api)
    stop.set()
    sent = sum(await asyncio.gather(*background))

    def line(label: str, values: list[float]) -> str:
        return (
            f"  {label}  p50 {statistics.median(values):.1f} ms  "
            f"p95 {percentile(values, 0.95):.1f} ms"
        )

    print(
        "\ningestion, 40 batches of 100 events (in-process):\n"
        + line("alone          ", quiet)
        + "\n"
        + line("with 4 deciders", busy)
        + f"  ({sent} decisions meanwhile)"
    )
    assert percentile(busy, 0.95) < 5 * max(percentile(quiet, 0.95), 50)
