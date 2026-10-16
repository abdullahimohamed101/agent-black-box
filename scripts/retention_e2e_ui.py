"""Gate 7 (the browser spec's data and its database checks) and gate 8 (cost) of scripts/retention-e2e.sh."""

import json
import os
import time
from pathlib import Path

import httpx

import retention_e2e_lib as lib
from retention_e2e_lib import check, gate, info


# ------------------------------------------------------------------------------------------------ gate 7: the browser


def ui_prepare() -> None:
    gate("gate7-ui-prepare")
    expired = lib.seeded("aged")[0].run
    available = lib.seeded("young")[0].run
    deleting = lib.seeded("young")[1]
    check(lib.trace_state(lib.owner(), expired) == "expired" and lib.trace_state(lib.owner(), available) == "available",
          "the real stack has a run in each of two trace states (expired by the job, available)")
    # The state in between exists in a real stack only while a batch is in flight (gate 3 observed it through the API). For the page, one
    # young run is put in it by the owner's SQL, and put back by ui-verify.
    lib.db("update runs set trace_state = 'deleting' where id = $1", deleting.uuid)
    check(lib.trace_state(lib.owner(), deleting.run) == "deleting", "a run reads trace_state deleting through the API")
    gone = lib.seeded("ancient")[0].run
    check(lib.run_view(lib.owner(), gone).status_code == 404, "a run past the run-record tier is 404 through the API")
    lib.save(ui={"gone": gone, "expired": expired, "available": available, "deleting": deleting.run, "deleting_uuid": str(deleting.uuid), "project": "main"})
    lib.finish()


def ui_verify() -> None:
    gate("gate7-ui-verify")
    ui = lib.state()["ui"]
    lib.db("update runs set trace_state = 'available' where id = $1", lib.uuid_of(ui["deleting"]))
    policy = lib.tier_days(lib.get_policy(lib.owner()))
    check(policy["artifacts"] == 10, "the browser saved the shortened artifacts period (14 -> 10 days) through the real API", policy=policy)
    actions = lib.audit_actions(lib.owner())
    check(actions.count("retention.policy.update") >= 4, "the browser's save is in the audit log", entries=actions.count("retention.policy.update"))
    check(lib.sqlstate_as(lib.runtime_url(), "select 1") is None, "stack still up")
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 8: cost


def _post_run(base: str, token: str, client: httpx.Client) -> tuple[float, int, int]:
    _, events = lib.run_events("probe-agent")
    started = time.perf_counter()
    try:
        response = client.post(f"{base}/v1/events/batch", json={"events": events}, headers=lib.bearer(token))
        status = response.status_code
        rejected = int(response.json().get("rejected", 0)) if status == 202 else 0
    except httpx.HTTPError:
        status, rejected = 0, 0
    return (time.perf_counter() - started) * 1000, status, rejected


def _summary(values: list[float]) -> dict[str, float]:
    return {"n": len(values), "p50_ms": round(lib.percentile(values, 0.5), 1), "p95_ms": round(lib.percentile(values, 0.95), 1)}


def gate8() -> None:
    gate("gate8-cost")
    rounds = int(os.environ.get("RETENTION_E2E_ROUNDS", "5"))
    per_round = int(os.environ.get("RETENTION_E2E_RUNS_PER_ROUND", "30"))
    idle_posts = int(os.environ.get("RETENTION_E2E_IDLE_POSTS", "60"))
    base, token = lib.H.main_url(), lib.state()["keys"]["bench"]
    ws = lib.workspace_uuid(lib.owner())
    owner = lib.owner()

    aged = lib.ingest_runs(base, token, "bench", "bench-aged", rounds * per_round)
    lib.prepare_group(ws, aged, 120, 1)
    lib.remember("bench-aged", aged)
    load_before = [round(x, 2) for x in os.getloadavg()]
    idle: list[float] = []
    busy: list[float] = []
    all_statuses: dict[str, list[int]] = {"idle": [], "busy": []}
    rejected_total = 0
    durations: list[float] = []
    posts_during: list[int] = []
    client = httpx.Client(timeout=60, limits=lib.H.NO_KEEPALIVE)
    for index in range(rounds):
        for _ in range(idle_posts):
            ms, status, rejected = _post_run(base, token, client)
            idle.append(ms)
            all_statuses["idle"].append(status)
            rejected_total += rejected
        # Enforcement in the background with the smallest transactions (one row per batch): the worst case for contention.
        started = time.perf_counter()
        proc = lib.start_retention("run-once", mode="enforce", name=f"bench-{index}", env={"ABB_RETENTION_MAX_RUNS_PER_TICK": str(per_round), "ABB_RETENTION_BATCH_SIZE": "1"})
        count = 0
        while proc.alive():
            ms, status, rejected = _post_run(base, token, client)
            busy.append(ms)
            all_statuses["busy"].append(status)
            rejected_total += rejected
            count += 1
        code = proc.wait()
        durations.append(time.perf_counter() - started)
        posts_during.append(count)
        expired = sum(1 for r in aged if lib.trace_state(owner, r.run) == "expired")
        check(code == 0 and expired == (index + 1) * per_round, f"round {index + 1}: the enforcement tick finished (exit 0) and {per_round} more runs expired", cumulative=expired)
    lib.wait_idle()
    bad = [s for s in all_statuses["idle"] + all_statuses["busy"] if s != 202]
    check(not bad and rejected_total == 0, "ingestion was accepted every time, idle and while enforcement ran: zero refusals, zero 5xx, zero rejected events",
          idle_posts=len(all_statuses["idle"]), busy_posts=len(all_statuses["busy"]), non_202=sorted(set(bad)))
    check(all(n >= 1 for n in posts_during), "ingestion kept being accepted during every enforcement tick", posts_during=posts_during)
    result = {"machine": lib.machine(), "load_before": load_before, "rounds": rounds, "runs_per_round": per_round, "idle_posts_per_round": idle_posts,
              "idle": _summary(idle), "during_enforcement": _summary(busy), "tick_seconds": [round(d, 2) for d in durations], "posts_during": posts_during}
    result["p50_ratio"] = round(result["during_enforcement"]["p50_ms"] / max(result["idle"]["p50_ms"], 0.001), 2)  # type: ignore[index]
    result["p95_ratio"] = round(result["during_enforcement"]["p95_ms"] / max(result["idle"]["p95_ms"], 0.001), 2)  # type: ignore[index]
    info(cost=result)
    out = os.environ.get("RETENTION_E2E_BENCH_OUT")
    if out:
        Path(out).write_text(json.dumps(result, indent=2))
    lib.finish()
