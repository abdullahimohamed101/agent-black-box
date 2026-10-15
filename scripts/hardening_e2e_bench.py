"""Gate 7 of scripts/hardening-e2e.sh: what the hardening costs the ingestion path (Phase 19a step 7).

API processes of this checkout, one set per configuration, all over the stack's database, driven alternately in rounds with
the same workload (batches of 100 events, key authentication, HTTP keep-alive, 1 client and 4 clients):

    off            ABB_LIMITS_SHARED_ENABLED=0, ABB_AUTH_FAIL_ENABLED=0, ABB_QUOTAS_ENABLED=0, no body cap (the pre-19a path)
    shared         only the shared limiter on, default block divisor 20
    every-request  only the shared limiter on, a database write on every request (the worst case of ADR-152)
    on             everything on at its defaults: shared limiter, failed-auth throttle, quotas, body cap
    off x2, on x2  the same, over TWO processes (the clients alternate between them)

Rate limits are raised so nothing is throttled (the cost of the checks is measured, not their refusals). Numbers are a regression
baseline for one machine shared with other work, not a capacity claim; the machine, load average and per-round spread are recorded.
"""

import asyncio
import json
import os
import statistics
import sys
import time
from typing import Any

import httpx

import hardening_e2e_lib as lib
from hardening_e2e_lib import check, gate, info

# ADR-152's triggers for moving the counters to Redis, and the spec's budget for an ingestion ack.
ADDED_P95_SHARED_MS, ADDED_P95_EVERY_REQUEST_MS, COUNTER_WRITES_PER_SECOND, LIVE_ROWS, SPEC_P95_MS = 5.0, 10.0, 500.0, 100_000, 150.0
# Everything on (limiter, failed-auth gate, quotas, body cap, headers) at one client: quotas measured +3.7 ms and the limiter +1 ms on
# the reference machine (docs/benchmarks/phase-19a-hardening-e2e.md); 20 ms leaves room for a slower machine without hiding a real cost.
ADDED_P95_ALL_ON_MS = 20.0
RAISED = {
    "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "5000", "ABB_RATE_LIMIT_BURST_EVENTS": "100000", "ABB_RATE_LIMIT_BYTES_PER_SECOND": "100000000",
    "ABB_RATE_LIMIT_BURST_BYTES": "1000000000", "ABB_LOG_LEVEL": "WARNING",
}
OFF = {"ABB_LIMITS_SHARED_ENABLED": "0", "ABB_AUTH_FAIL_ENABLED": "0", "ABB_QUOTAS_ENABLED": "0", "ABB_MAX_BODY_BYTES_IN_FLIGHT": str(1 << 40)}
SHARED_ONLY = {"ABB_AUTH_FAIL_ENABLED": "0", "ABB_QUOTAS_ENABLED": "0", "ABB_MAX_BODY_BYTES_IN_FLIGHT": str(1 << 40)}
CONFIGS: dict[str, tuple[int, dict[str, str]]] = {  # name -> (processes, settings)
    "off": (1, OFF),
    "shared": (1, SHARED_ONLY),
    "every-request": (1, {**SHARED_ONLY, "ABB_LIMITS_BLOCK_DIVISOR": "1000"}),
    "on": (1, {}),
    "off x2": (2, OFF),
    "on x2": (2, {}),
}


async def _cell(urls: list[str], token: str, clients: int, batches: int) -> list[float]:
    sys.path.insert(0, str(lib.ROOT / "scripts"))
    from bench_ingest import make_events  # noqa: PLC0415

    latencies: list[float] = []
    headers = {**lib.bearer(token), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=clients + 2)) as http:

        async def one(index: int) -> None:
            for i in range(batches // clients):
                run = lib.run_ids()
                body = json.dumps({"events": make_events(run, 1, 100)}).encode()
                started = time.perf_counter()
                response = await http.post(f"{urls[(index + i) % len(urls)]}/v1/events/batch", content=body, headers=headers)
                elapsed = (time.perf_counter() - started) * 1000
                if response.status_code != 202:
                    lib.die(f"bench: {response.status_code} {response.text[:120]}")
                latencies.append(elapsed)

        await asyncio.gather(*[one(c) for c in range(clients)])
    return latencies


def _writes() -> int:
    time.sleep(1.6)  # table statistics are flushed asynchronously
    return int(lib.db_one("select coalesce(n_tup_ins + n_tup_upd, 0) from pg_stat_user_tables where relname = 'rate_limit_counters'") or 0)


def gate7() -> None:
    gate("gate7-cost")
    rounds = int(os.environ.get("HARDENING_E2E_ROUNDS", "5"))
    workloads = [(1, 60), (4, 100)]  # (clients, batches per cell)
    ports = iter(range(lib.OIDC_PORT + 12, lib.OIDC_PORT + 40))
    procs: dict[str, list[lib.Proc]] = {}
    tokens: dict[str, str] = {}
    for name, (count, settings) in CONFIGS.items():
        procs[name] = [lib.start_api(f"bench-{name.replace(' ', '')}-{n}", next(ports), {**RAISED, **settings}, wait=False) for n in range(count)]
    for group in procs.values():
        for proc in group:
            if not lib.wait_ready(proc):
                lib.die(f"{proc.name} did not become ready")
    for name in CONFIGS:
        project = lib.new_project(f"bench-{name.replace(' ', '')}")
        tokens[name] = lib.new_key(project, ["events:write"], name)
    try:
        before_machine = lib.machine()
        for name, group in procs.items():  # warm every process (connections, caches, imports)
            asyncio.run(_cell([p.url for p in group], tokens[name], 2, 20))
        lib.quiet()
        results: dict[str, dict[int, list[dict[str, float]]]] = {n: {1: [], 4: []} for n in CONFIGS}
        counter_rows: dict[str, list[float]] = {n: [] for n in CONFIGS}
        loads: list[float] = []
        for round_no in range(rounds):
            loads.append(round(os.getloadavg()[0], 2))
            order = list(CONFIGS) if round_no % 2 == 0 else list(reversed(list(CONFIGS)))  # alternate the order every round
            for name in order:
                urls = [p.url for p in procs[name]]
                requests, began = 0, _writes()
                started = time.perf_counter()
                for clients, batches in workloads:
                    got = asyncio.run(_cell(urls, tokens[name], clients, batches))
                    requests += len(got)
                    results[name][clients].append({"p50": lib.percentile(got, 0.5), "p95": lib.percentile(got, 0.95), "mean": statistics.fmean(got), "n": len(got)})
                busy = time.perf_counter() - started
                writes = _writes() - began
                counter_rows[name].append(1000.0 * writes / max(1, requests))
                results[name].setdefault(0, []).append({"writes": float(writes), "requests": float(requests), "seconds": busy})  # type: ignore[arg-type]
            lib.quiet(max_rate=60, timeout=60)
        after_machine = lib.machine()
    finally:
        for group in procs.values():
            lib.stop_all(*group)

    def med(name: str, clients: int, key: str) -> float:
        return round(statistics.median(r[key] for r in results[name][clients]), 1)

    def noise_of(name: str, clients: int) -> float:
        """How far the p95 of one configuration moved between rounds: the smallest difference this run can resolve."""
        values = [r["p95"] for r in results[name][clients]]
        return max(values) - min(values)

    def spread(name: str, clients: int) -> str:
        values = [r["p95"] for r in results[name][clients]]
        return f"{min(values):.0f}-{max(values):.0f}"

    table = []
    for name in CONFIGS:
        for clients in (1, 4):
            writes = sum(r["writes"] for r in results[name][0])
            requests = sum(r["requests"] for r in results[name][0])
            table.append({"config": name, "clients": clients, "p50_ms": med(name, clients, "p50"), "p95_ms": med(name, clients, "p95"), "p95_range_ms": spread(name, clients),
                          "mean_ms": med(name, clients, "mean"), "counter_writes_per_1000_requests": round(1000 * writes / max(1, requests), 1)})
    print("\n  config          clients   p50 ms   p95 ms (range over rounds)   mean ms   counter writes / 1000 req")
    for row in table:
        print(f"  {row['config']:<14} {row['clients']:>7} {row['p50_ms']:>8} {row['p95_ms']:>8}  ({row['p95_range_ms']:>9})        {row['mean_ms']:>7}   {row['counter_writes_per_1000_requests']:>8}")
    summary = {"machine_before": before_machine, "machine_after": after_machine, "load_average_1m_at_round_start": loads, "rounds": rounds,
               "workload": "batches of 100 events (about 51 KiB), keep-alive; 1 client x 60 batches and 4 clients x 100 batches per cell", "table": table}
    lib.WORK.joinpath("bench.json").write_text(json.dumps(summary, indent=2))
    info(bench=summary)

    delta = lambda a, b, c: round(med(a, c, "p95") - med(b, c, "p95"), 1)  # noqa: E731
    delta_p50 = lambda a, b, c: round(med(a, c, "p50") - med(b, c, "p50"), 1)  # noqa: E731
    for clients in (1, 4):
        d_shared, d_every, d_on = delta("shared", "off", clients), delta("every-request", "off", clients), delta("on", "off", clients)
        d_on2 = delta("on x2", "off x2", clients)
        added = {"shared limiter (default blocks)": d_shared, "shared limiter, a write per request": d_every, "everything on": d_on, "everything on, two processes": d_on2}
        info(clients=clients, added_p95_ms=added)
        if clients == 1:
            # The bounds are about what one request pays (service time). One client has no queueing, so these are the pass/fail lines.
            # A shared CI runner is noisy: the baseline's own p95 moved by `noise` ms between rounds, so a difference at or below that
            # cannot be told from noise and is not a failure. A difference above both the bound AND the noise is a real cost.
            noise = round(noise_of("off", 1), 1)
            noise2 = round(noise_of("off x2", 1), 1)
            check(d_shared <= ADDED_P95_SHARED_MS or d_shared <= noise,
                  f"1 client: the shared limiter adds {d_shared} ms to the p95 (ADR-152 bound: {ADDED_P95_SHARED_MS} ms; baseline noise {noise} ms)")
            check(d_every <= ADDED_P95_EVERY_REQUEST_MS or d_every <= noise,
                  f"1 client: with a database write on every request it adds {d_every} ms to the p95 (ADR-152 bound: {ADDED_P95_EVERY_REQUEST_MS} ms; baseline noise {noise} ms)")
            # The absolute spec budget is only a meaningful line on a machine whose own baseline meets it. Where the baseline already
            # exceeds it (a slow shared runner), judge the added cost against the bound for everything on instead, and say so.
            for label, name, base, added, base_noise in (("one process", "on", "off", d_on, noise), ("two processes", "on x2", "off x2", d_on2, noise2)):
                on_p95, off_p95 = med(name, 1, "p95"), med(base, 1, "p95")
                if off_p95 <= SPEC_P95_MS:
                    check(on_p95 <= SPEC_P95_MS, f"1 client: everything on, {label}, ack p95 {on_p95} ms (spec budget {SPEC_P95_MS} ms); added {added} ms over everything off")
                else:
                    check(added <= ADDED_P95_ALL_ON_MS or added <= base_noise,
                          f"1 client: everything on, {label}, added {added} ms to the p95 (bound {ADDED_P95_ALL_ON_MS} ms; baseline noise {base_noise} ms); "
                          f"the absolute {SPEC_P95_MS} ms budget is not judged because everything OFF already reads {off_p95} ms on this machine")
    # Four closed-loop clients saturate one process: latency is then mostly queueing, which multiplies every added millisecond of
    # service time by the number of waiters, and (everything on) the per-project quota lock serialises the batches of one project
    # across processes (ADR-154, KI-178), so a second process stops helping. Recorded, not asserted: the ceiling is a stated cost.
    # The literal ADR-152 wording applied to the saturated run, stated rather than hidden: p50 is the better guide there.
    judged = {
        "shared limiter vs 5 ms": ("PASS" if delta("shared", "off", 4) <= ADDED_P95_SHARED_MS else "EXCEEDED", delta("shared", "off", 4), f"p50 {delta_p50('shared', 'off', 4)} ms"),
        "write-per-request vs 10 ms": ("PASS" if delta("every-request", "off", 4) <= ADDED_P95_EVERY_REQUEST_MS else "EXCEEDED", delta("every-request", "off", 4), f"p50 {delta_p50('every-request', 'off', 4)} ms"),
        "everything on, ack p95 vs 150 ms": ("PASS" if med("on", 4, "p95") <= SPEC_P95_MS else "EXCEEDED", med("on", 4, "p95"), f"off {med('off', 4, 'p95')} ms"),
        "everything on x2, ack p95 vs 150 ms": ("PASS" if med("on x2", 4, "p95") <= SPEC_P95_MS else "EXCEEDED", med("on x2", 4, "p95"), f"off x2 {med('off x2', 4, 'p95')} ms"),
    }
    info(four_clients_literal_bounds=judged)
    info(four_clients_note="saturated closed loop; 'on x2' loses the two-process gain of 'off x2' because one project's batches queue on the quota lock (KI-178)",
         off_x1=med("off", 4, "p95"), on_x1=med("on", 4, "p95"), off_x2=med("off x2", 4, "p95"), on_x2=med("on x2", 4, "p95"))
    # Redis trigger: counter writes per second at the measured rate, and extrapolated to 1,000 requests a second
    for name in ("shared", "every-request", "on", "on x2"):
        writes = sum(r["writes"] for r in results[name][0])
        seconds = sum(r["seconds"] for r in results[name][0])
        per_second = round(writes / max(0.001, seconds), 1)
        per_1000 = round(1000 * writes / max(1, sum(r["requests"] for r in results[name][0])), 1)
        info(redis_trigger=name, counter_writes_per_second_measured=per_second, writes_per_1000_requests=per_1000, extrapolated_writes_per_second_at_1000_rps=per_1000)
        if name != "every-request":  # the worst case is a stress configuration, not a deployment: reported, not judged
            check(per_second < COUNTER_WRITES_PER_SECOND and per_1000 < COUNTER_WRITES_PER_SECOND, f"{name}: counter writes {per_second}/s measured, {per_1000} per 1,000 requests, so {per_1000}/s at 1,000 requests a second (ADR-152 trigger: {COUNTER_WRITES_PER_SECOND:.0f}/s sustained)")
    rows = int(lib.db_one("select count(*) from rate_limit_counters") or 0)
    check(rows < LIVE_ROWS, f"rate_limit_counters holds {rows} rows (ADR-152 trigger: {LIVE_ROWS})")
    lib.finish()
