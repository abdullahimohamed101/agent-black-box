"""Gate 7 of scripts/obs-e2e.sh (D9 gate 8, first half): what metrics and tracing cost an ingestion batch. INFORMATIONAL: timings are printed
and written to a file for docs/benchmarks/phase-19c-obs-e2e.md, never asserted. What IS asserted is deterministic: every request in
every cell was a 202 (zero 5xx, zero refusals), and the number of events each variant stored equals the number it was sent.

Three API processes of this checkout over the stack's database, driven alternately in rounds with one workload (batches of 100 events,
key authentication, HTTP keep-alive, one client and four clients):

    default      the default install: no OpenTelemetry in the environment, no metrics token (no /metrics route), nobody scraping
    otel-off     the `otel` environment, tracing OFF, no token: the cost of merely having the extra installed
    on           the `otel` environment, tracing ON at sampling 1.0 exporting to a local receiver, a metrics token, /metrics scraped every second

The registry counts in every variant (it has no off switch by design: the counters are the SLI), so "metrics off" means no endpoint and
no scraper. Rate limits are raised so nothing is throttled; the log level is WARNING so logging is not part of the comparison.
"""

import asyncio
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, gate, info

RAISED = {
    "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "5000", "ABB_RATE_LIMIT_BURST_EVENTS": "100000", "ABB_RATE_LIMIT_BYTES_PER_SECOND": "100000000",
    "ABB_RATE_LIMIT_BURST_BYTES": "1000000000", "ABB_LOG_LEVEL": "WARNING", "ABB_QUOTAS_ENABLED": "0",
}
WORKLOADS = [(1, 60), (4, 100)]  # (clients, batches per cell)
EVENTS_PER_BATCH = 100


async def _cell(url: str, token: str, clients: int, batches: int, tally: dict[str, int]) -> list[float]:
    sys.path.insert(0, str(lib.ROOT / "scripts"))
    from bench_ingest import make_events  # noqa: PLC0415

    latencies: list[float] = []
    headers = {**lib.bearer(token), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=clients + 2)) as http:

        async def one() -> None:
            for _ in range(batches // clients):
                run = lib.run_ids()
                body = json.dumps({"events": make_events(run, 1, EVENTS_PER_BATCH)}).encode()
                started = time.perf_counter()
                response = await http.post(f"{url}/v1/events/batch", content=body, headers=headers)
                elapsed = (time.perf_counter() - started) * 1000
                tally[str(response.status_code)] = tally.get(str(response.status_code), 0) + 1
                if response.status_code == 202:
                    tally["events_sent"] = tally.get("events_sent", 0) + EVENTS_PER_BATCH
                latencies.append(elapsed)

        await asyncio.gather(*[one() for _ in range(clients)])
    return latencies


def gate7() -> None:
    gate("gate7-cost")
    rounds = int(os.environ.get("OBS_E2E_ROUNDS", "5"))
    receiver_port = lib.free_port()
    receiver = subprocess.Popen([lib.OTEL_PYTHON, str(Path(__file__).parent / "obs_e2e_otlp.py"), "--port", str(receiver_port), "--discard"],  # noqa: S603
                                stdout=(lib.WORK / "otlp-bench.out").open("wb"), stderr=subprocess.STDOUT, start_new_session=True)
    lib.poll("the sink receiver listens", lambda: receiver.poll() is None and "listening" in (lib.WORK / "otlp-bench.out").read_text(), timeout=30, every=0.2)
    otel_env = {"ABB_OTEL_ENABLED": "1", "ABB_OTEL_SAMPLE_RATIO": "1.0", "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{receiver_port}"}
    variants: dict[str, tuple[str | None, dict[str, str | None]]] = {
        "default": (None, {**RAISED, "ABB_METRICS_TOKEN": None, "ABB_WORKER_METRICS_PORT": None}),
        "otel-off": (lib.OTEL_PYTHON, {**RAISED, "ABB_METRICS_TOKEN": None, "ABB_WORKER_METRICS_PORT": None}),
        "on": (lib.OTEL_PYTHON, {**RAISED, **otel_env}),
    }
    procs: dict[str, lib.Proc] = {}
    tokens: dict[str, str] = {}
    sent: dict[str, dict[str, int]] = {name: {} for name in variants}
    stop = threading.Event()
    scrapes = {"count": 0, "failed": 0}
    try:
        for index, (name, (python, env)) in enumerate(variants.items()):
            procs[name] = lib.start_server(f"bench-{name}", lib.API_PORT + 14 + index, env, python=python, wait=False)
        for proc in procs.values():
            if not lib.base.wait_ready(proc):
                lib.die(f"{proc.name} did not become ready")
        projects = {name: lib.new_project(f"bench-{name}") for name in variants}
        for name in variants:
            tokens[name] = lib.new_key(projects[name], ["events:write"], f"bench-{name}")
            lib.remember_secret(f"api-key-bench-{name}", tokens[name])

        def scraper() -> None:
            while not stop.is_set():
                try:
                    scrapes["count"] += 1
                    if lib.scrape_raw(procs["on"].url).status_code != 200:
                        scrapes["failed"] += 1
                except httpx.HTTPError:
                    scrapes["failed"] += 1
                stop.wait(1.0)

        threading.Thread(target=scraper, daemon=True).start()
        before_machine = lib.base.machine()
        for name, proc in procs.items():  # warm every process (connections, caches, imports, the first export)
            asyncio.run(_cell(proc.url, tokens[name], 2, 20, {}))
            sent[name] = {}
        results: dict[str, dict[int, list[dict[str, float]]]] = {n: {1: [], 4: []} for n in variants}
        loads: list[float] = []
        orders: list[list[str]] = []
        for round_no in range(rounds):
            loads.append(round(os.getloadavg()[0], 2))
            order = list(variants) if round_no % 2 == 0 else list(reversed(list(variants)))  # alternate the order every round
            orders.append(order)
            for name in order:
                for clients, batches in WORKLOADS:
                    got = asyncio.run(_cell(procs[name].url, tokens[name], clients, batches, sent[name]))
                    results[name][clients].append({"p50": lib.base.percentile(got, 0.5), "p95": lib.base.percentile(got, 0.95), "mean": statistics.fmean(got), "n": len(got)})
        after_machine = lib.base.machine()
    finally:
        stop.set()
        for proc in procs.values():
            proc.stop()
        if receiver.poll() is None:
            receiver.terminate()

    # ------------------------------------------------------------------ deterministic facts
    expected_batches = rounds * sum(batches for _, batches in WORKLOADS)
    for name in variants:
        statuses = {k: v for k, v in sent[name].items() if k != "events_sent"}
        check(statuses == {"202": expected_batches}, f"{name}: all {expected_batches} measured batches were 202 (zero 5xx, zero refusals)", statuses=statuses)
        stored = int(lib.base.db_one("select count(*) from events where project_id = $1", lib.base.uuid_of(projects[name])) or 0)
        warmup = 2 * 20 * EVENTS_PER_BATCH // 2  # the warm-up cell: 2 clients x 10 batches each
        check(stored == sent[name].get("events_sent", 0) + warmup, f"{name}: the events stored equal the events sent (measured + warm-up)", stored=stored, sent=sent[name].get("events_sent"), warmup=warmup)
    check(scrapes["count"] > 0 and scrapes["failed"] == 0, "on: every scrape of /metrics during the run was a 200", scrapes=scrapes["count"], failed=scrapes["failed"])

    # ------------------------------------------------------------------ the informational table
    def med(name: str, clients: int, key: str) -> float:
        return round(statistics.median(r[key] for r in results[name][clients]), 1)

    def spread(name: str, clients: int) -> str:
        values = [r["p95"] for r in results[name][clients]]
        return f"{min(values):.0f}-{max(values):.0f}"

    table = []
    for name in variants:
        for clients in (1, 4):
            table.append({"variant": name, "clients": clients, "p50_ms": med(name, clients, "p50"), "p95_ms": med(name, clients, "p95"), "p95_range_ms": spread(name, clients),
                          "mean_ms": med(name, clients, "mean")})
    print("\n  variant    clients   p50 ms   p95 ms (range over rounds)   mean ms")
    for row in table:
        print(f"  {row['variant']:<10} {row['clients']:>7} {row['p50_ms']:>8} {row['p95_ms']:>8}  ({row['p95_range_ms']:>9})        {row['mean_ms']:>7}")
    summary: dict[str, Any] = {"machine_before": before_machine, "machine_after": after_machine, "load_average_1m_at_round_start": loads, "rounds": rounds, "orders": orders,
                               "workload": f"batches of {EVENTS_PER_BATCH} events, keep-alive; 1 client x 60 batches and 4 clients x 100 batches per cell", "table": table}
    (lib.WORK / "bench.json").write_text(json.dumps(summary, indent=2))
    destination = os.environ.get("OBS_E2E_BENCH_JSON")
    if destination:
        Path(destination).write_text(json.dumps(summary, indent=2))
    lib.timing(bench=summary)
    info(on_vs_default_p50_ms_1_client=round(med("on", 1, "p50") - med("default", 1, "p50"), 1), on_vs_default_p95_ms_1_client=round(med("on", 1, "p95") - med("default", 1, "p95"), 1),
         otel_installed_but_off_vs_default_p50_ms_1_client=round(med("otel-off", 1, "p50") - med("default", 1, "p50"), 1))
    lib.finish()
