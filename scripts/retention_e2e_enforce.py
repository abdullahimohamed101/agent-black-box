"""Gates 1-4 of scripts/retention-e2e.sh: dry run, enforcement, crash safety, kill switch and bounds, on a real stack (ADR-159, ADR-160)."""

import signal
import uuid
from typing import Any

import retention_e2e_lib as lib
from retention_e2e_lib import check, gate, info

DETAIL_TABLES = ("events", "spans", "cost_calculations", "findings", "run_findings", "evaluations", "run_search")
# Tables the dry run must leave exactly as they were (it may only add rows to retention_reports).
EVENTS_PER_RUN = 4
ARTIFACTS_PER_RUN = 2
BYTES_PER_RUN = 1500 + 1800


def ids_of(*groups: str) -> list[uuid.UUID]:
    return [r.uuid for g in groups for r in lib.seeded(g)]


def rows_digest(run_ids: list[uuid.UUID]) -> dict[str, str]:
    """A digest per table of every row that belongs to these runs (volatile columns removed)."""
    out = {name: lib.table_digest(name, "run_id = any($1::uuid[])", run_ids) for name in (*DETAIL_TABLES, "artifacts")}
    out["runs"] = lib.table_digest("runs", "id = any($1::uuid[])", run_ids)
    return out


def counts_of(run_ids: list[uuid.UUID]) -> dict[str, int]:
    out = {name: int(lib.db_one(f"select count(*) from {name} where run_id = any($1::uuid[])", run_ids)) for name in (*DETAIL_TABLES, "artifacts")}  # noqa: S608
    out["runs"] = int(lib.db_one("select count(*) from runs where id = any($1::uuid[])", run_ids))
    return out


def summary_of(proc: lib.RetentionProc) -> dict[str, str]:
    return proc.summary()


def tombstones(ws: uuid.UUID, run_ids: list[uuid.UUID]) -> dict[str, int]:
    rows = lib.db("select tier, count(*) from retention_tombstones where workspace_id = $1 and run_id = any($2::uuid[]) group by tier", ws, run_ids)
    return {r[0]: int(r[1]) for r in rows}


def signature(run_id: uuid.UUID) -> tuple[Any, ...]:
    """What is left of one run: its trace state, its row counts per detail table and artifacts, and the tiers it has tombstones for."""
    state = lib.db_one("select trace_state from runs where id = $1", run_id)
    counts = tuple(int(lib.db_one(f"select count(*) from {name} where run_id = $1", run_id)) for name in (*DETAIL_TABLES, "artifacts"))  # noqa: S608
    tiers = tuple(sorted(r[0] for r in lib.db("select tier from retention_tombstones where run_id = $1", run_id)))
    return (state, counts, tiers)


# ------------------------------------------------------------------------------------------------ gate 1


def gate1() -> None:
    gate("gate1-dry-run")
    ws_a, ws_b = lib.workspace_uuid(lib.owner()), lib.workspace_uuid(lib.other_owner())
    before_counts = lib.counts()
    everything = [r.uuid for g in lib.state()["groups"] for r in lib.seeded(g)]
    before_digest = rows_digest(everything)
    before_files = lib.files_digest()
    code, proc = lib.run_retention("run-once", mode="dry_run")
    summary = summary_of(proc)
    info(exit=code, summary=summary)
    check(code == 0 and summary.get("mode") == "dry_run", "run-once in dry_run mode exits 0")
    check(summary.get("deleted_runs") == "0" and summary.get("expired_traces") == "0" and summary.get("deleted_events") == "0",
          "the tick reports it deleted nothing", summary=summary)

    after_counts = lib.counts()
    changed = {t: (before_counts[t], after_counts[t]) for t in after_counts if before_counts[t] != after_counts[t]}
    check(set(changed) == {"retention_reports"} and changed["retention_reports"][1] > changed["retention_reports"][0],
          "only retention_reports gained rows: every other table has the same row count", changed=changed)
    check(rows_digest(everything) == before_digest, "the content of every run's rows (events, spans, cost, findings, search, artifacts, runs) is unchanged")
    check(lib.files_digest() == before_files and len(before_files) > 0, "the artifact directory is byte-identical (every file, by sha256)", files=len(before_files))
    check(lib.db_one("select count(*) from retention_tombstones") == 0 and lib.db_one("select count(*) from retention_run_progress") == 0,
          "no tombstone and no progress row exist")

    # Expected counts, computed independently from the rows I seeded (not from the job).
    old_artifacts = ids_of("mid", "aged", "ancient")
    old_trace = ids_of("aged", "ancient")
    expected_a = {
        "artifacts": {"runs": len(old_artifacts), "artifact_bytes": len(old_artifacts) * BYTES_PER_RUN, "rows": len(old_artifacts) * ARTIFACTS_PER_RUN},
        "trace": {"runs": len(old_trace), "events": len(old_trace) * EVENTS_PER_RUN},
        "run": {"runs": len(ids_of("ancient"))},
    }
    expected_b = {"artifacts": {"runs": 6, "artifact_bytes": 6 * BYTES_PER_RUN}, "trace": {"runs": 6, "events": 6 * EVENTS_PER_RUN}}
    for label, client, expected in (("workspace A", lib.owner(), expected_a), ("workspace B (default policy)", lib.other_owner(), expected_b)):
        latest = {r["tier"]: r for r in lib.reports(client) if r["mode"] == "dry_run"}
        for tier, wanted in expected.items():
            got = {k: latest.get(tier, {}).get(k) for k in wanted}
            check(got == wanted, f"{label}: the {tier} report counts exactly what is expired", reported=got, expected=wanted)
        check(all(not r["truncated"] for r in latest.values()), f"{label}: no report is truncated")
    check(all(r["mode"] == "dry_run" for r in lib.reports(lib.owner())), "every report says dry_run")
    # The run tier never counts runs of the young groups or of the other workspace.
    check(int(lib.db_one("select coalesce(sum(runs), 0) from retention_reports where workspace_id = $1 and tier = 'run'", ws_b)) == 0,
          "workspace B reports no run-tier runs (nothing of it is older than a year)")
    check(ws_a != ws_b, "two workspaces")
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 2


def gate2() -> None:
    gate("gate2-enforce")
    owner, other = lib.owner(), lib.other_owner()
    ws_a, ws_b = lib.workspace_uuid(owner), lib.workspace_uuid(other)
    base = lib.H.main_url()

    # ---- the policy through the real API: role-gated, floor, audited, isolated
    viewer, developer, admin = lib.H.person("viewer"), lib.H.person("developer"), lib.H.person("admin")
    check(lib.put_policy(viewer, trace=60).status_code == 403, "a viewer cannot change the policy (403)")
    check(lib.put_policy(developer, trace=60).status_code == 403, "a developer cannot change the policy (403)")
    refused = lib.put_policy(owner, artifacts=3)
    check(refused.status_code == 422, "a 3-day period is refused: the floor is 7 days", status=refused.status_code, code=refused.json().get("error", {}).get("code"))
    check(lib.put_policy(owner, artifacts=10, trace=5).status_code == 422, "a trace period below the floor is refused in a multi-tier change")
    check(lib.put_policy(owner, artifacts=200, trace=60).status_code == 422, "artifacts may not outlast trace detail (order rule)")
    check(lib.tier_days(lib.get_policy(owner))["artifacts"] == 30, "a refused change changes nothing", policy=lib.tier_days(lib.get_policy(owner)))
    done = lib.put_policy(admin, artifacts=14, trace=60, run=300)
    check(done.status_code == 200 and lib.tier_days(done.json()) == {"artifacts": 14, "trace": 60, "run": 300, "audit": 365},
          "an admin sets artifacts 14, trace 60, run 300 days", policy=lib.tier_days(done.json()))
    forever = lib.put_policy(other, artifacts="forever", trace="forever", run="forever")
    check(forever.status_code == 200, "workspace B's owner keeps everything forever")
    check(lib.tier_days(lib.get_policy(owner))["artifacts"] == 14 and lib.tier_days(lib.get_policy(other))["artifacts"] == "forever",
          "the two workspaces' policies are independent")
    foreign = owner.get(f"/v1/workspaces/{lib.workspace_public_id(other)}/retention")
    check(foreign.status_code in {403, 404}, "workspace A's owner cannot read workspace B's policy", status=foreign.status_code)
    actions = lib.audit_actions(owner)
    check(actions.count("retention.policy.update") >= 3, "each changed tier wrote an audit entry (retention.policy.update)", entries=actions.count("retention.policy.update"))

    # ---- before
    ancient, aged, mid, young = lib.seeded("ancient"), lib.seeded("aged"), lib.seeded("mid"), lib.seeded("young")
    untouched_ids = ids_of("young", "sibling", "doomed", "c_young", "b_aged")
    untouched_before = rows_digest(untouched_ids)
    mid_before = counts_of([r.uuid for r in mid])
    files_before = lib.files_digest()
    expired_keys = lib.run_keys([r.uuid for r in (*mid, *aged, *ancient)])
    kept_keys = lib.run_keys(untouched_ids)
    main = lib.state()["project_ids"]["main"]
    bytes_before = lib.project_artifact_bytes(owner, main)
    stored_before = int(lib.db_one("select coalesce(sum(size_bytes), 0) from artifacts where project_id = $1", lib.uuid_of(main)))
    check(bytes_before == stored_before, "the quota counter equals the stored artifact bytes before enforcement", counter=bytes_before, stored=stored_before)
    deleted_bytes = int(lib.db_one("select coalesce(sum(size_bytes), 0) from artifacts where run_id = any($1::uuid[])", [r.uuid for r in (*mid, *aged, *ancient)]))
    aged_days = lib.run_days(aged) | lib.run_days(ancient)
    day_aged, day_ancient = sorted(lib.run_days(aged)), sorted(lib.run_days(ancient))
    spans_before = int(lib.db_one("select count(*) from analytics_spans_daily where workspace_id = $1 and day = any($2::date[])", ws_a, list(aged_days)))
    cost_before = int(lib.db_one("select count(*) from analytics_cost_daily where workspace_id = $1 and day = any($2::date[])", ws_a, list(aged_days)))
    check(spans_before > 0 and cost_before > 0, "before: the rollups of the old days hold span and cost rows", spans=spans_before, cost=cost_before)
    check(all(lib.file_exists(k) for k in expired_keys), "before: every artifact file exists", files=len(expired_keys))
    check(lib.trace_state(owner, aged[0].run) == "available", "before: an aged run reads trace_state available")

    # ---- enforce
    code, proc = lib.run_retention("run-once", mode="enforce")
    summary = summary_of(proc)
    info(exit=code, summary=summary)
    check(code == 0 and summary.get("mode") == "enforce" and summary.get("failed") == "0", "run-once in enforce mode exits 0 with failed=0")
    check(summary.get("deleted_runs") == str(len(ancient)) and summary.get("expired_traces") == str(len(aged) + len(ancient)),
          "it removed 6 run records and expired 18 traces", summary={k: summary.get(k) for k in ("deleted_runs", "expired_traces", "deleted_events")})
    check(summary.get("deleted_events") == str((len(aged) + len(ancient)) * EVENTS_PER_RUN), "it deleted exactly the events of the 18 runs", got=summary.get("deleted_events"))

    # ---- what is gone, what is left
    ancient_ids, aged_ids, mid_ids = [r.uuid for r in ancient], [r.uuid for r in aged], [r.uuid for r in mid]
    check(set(counts_of(ancient_ids).values()) == {0}, "ancient runs (past the run-record tier): no run row, no events, no spans, no artifacts, nothing", counts=counts_of(ancient_ids))
    signature_aged = {signature(i) for i in aged_ids}
    check(signature_aged == {("expired", (0,) * 8, ("artifacts", "trace"))}, "aged runs: record kept, trace_state expired, every detail row and artifact gone, tombstones for artifacts and trace",
          signatures=sorted(map(str, signature_aged)))
    mid_after = counts_of(mid_ids)
    check(mid_after["artifacts"] == 0 and {k: v for k, v in mid_after.items() if k != "artifacts"} == {k: v for k, v in mid_before.items() if k != "artifacts"},
          "mid runs: artifacts gone, events and the rest byte-for-byte present", before=mid_before, after=mid_after)
    check({lib.trace_state(owner, r.run) for r in mid} == {"available"}, "mid runs still read trace_state available")
    check(not any(lib.file_exists(k) for k in expired_keys), "every expired artifact FILE is gone from the artifact directory", removed=len(expired_keys))
    check(all(lib.file_exists(k) for k in kept_keys), "every unexpired artifact file is still there", kept=len(kept_keys))
    check(rows_digest(untouched_ids) == untouched_before, "unexpired runs (young, sibling, doomed, workspace C) and ALL of workspace B are byte-identical")
    files_after = lib.files_digest()
    check({k: v for k, v in files_before.items() if k not in set(expired_keys)} == files_after, "the artifact directory lost exactly the expired files and nothing else")

    # ---- the order, observed in the tombstones
    stones = lib.db("select run_id, tier, deleted_at from retention_tombstones where run_id = any($1::uuid[])", ancient_ids)
    by_run: dict[uuid.UUID, dict[str, Any]] = {}
    for row in stones:
        by_run.setdefault(row[0], {})[row[1]] = row[2]
    ordered = [t.get("artifacts") <= t.get("trace") <= t.get("run") for t in by_run.values() if {"artifacts", "trace", "run"} <= set(t)]
    check(len(ordered) == len(ancient) and all(ordered), "tombstones show the order artifacts <= trace detail <= run record for every ancient run", runs=len(ordered))
    check(tombstones(ws_a, aged_ids + ancient_ids + mid_ids) == {"artifacts": 24, "trace": 18, "run": 6}, "tombstones: 24 artifact, 18 trace, 6 run, no duplicates",
          got=tombstones(ws_a, aged_ids + ancient_ids + mid_ids))
    check(int(lib.db_one("select count(*) from retention_run_progress")) == 0, "no progress row is left: every step completed")

    # ---- the API
    sample = aged[0].run
    view = lib.run_view(owner, sample)
    check(view.status_code == 200 and view.json()["trace_state"] == "expired", "API: an aged run reads trace_state expired", state=view.json().get("trace_state"))
    check(owner.get(f"/v1/runs/{sample}/events", params={"limit": 50}).json()["items"] == [], "API: its events are empty, never partial")
    compare = owner.get(f"/v1/runs/{sample}/compare", params={"with_run_id": sample})
    check(compare.status_code == 409 and compare.json()["error"]["code"] == "TRACE_EXPIRED", "API: compare answers 409 TRACE_EXPIRED", status=compare.status_code)
    evaluate = owner.post(f"/v1/runs/{sample}/evaluations", json={"evaluator_id": str(lib.H.new_id(lib.H.IdKind.EVALUATOR))})
    check(evaluate.status_code == 409 and evaluate.json()["error"]["code"] == "TRACE_EXPIRED", "API: evaluate answers 409 TRACE_EXPIRED", status=evaluate.status_code)
    check(lib.run_view(owner, ancient[0].run).status_code == 404, "API: a run past the run-record tier is 404")
    check({lib.trace_state(owner, r.run) for r in young} == {"available"}, "API: young runs read trace_state available")

    # ---- the quota counter, rollups, audit, reports
    bytes_after = lib.project_artifact_bytes(owner, main)
    check(bytes_before - bytes_after == deleted_bytes and deleted_bytes == len(mid + aged + ancient) * BYTES_PER_RUN,
          "the artifact quota counter decreased by exactly the deleted bytes", before=bytes_before, after=bytes_after, deleted=deleted_bytes)
    spans_after = int(lib.db_one("select count(*) from analytics_spans_daily where workspace_id = $1 and day = any($2::date[])", ws_a, list(aged_days)))
    cost_after = int(lib.db_one("select count(*) from analytics_cost_daily where workspace_id = $1 and day = any($2::date[])", ws_a, list(aged_days)))
    check(spans_after == 0 and cost_after == 0, "the rollups of the old days no longer include the deleted detail (spans, cost by model)", spans=spans_after, cost=cost_after)
    runs_daily = lib.db("select coalesce(sum(runs), 0), coalesce(sum(expired_trace_runs), 0) from analytics_runs_daily where workspace_id = $1 and day = any($2::date[])", ws_a, day_aged)
    ancient_daily = lib.db_one("select coalesce(sum(runs), 0) from analytics_runs_daily where workspace_id = $1 and day = any($2::date[])", ws_a, day_ancient)
    check(runs_daily[0][0] >= len(aged) and runs_daily[0][1] >= len(aged), "the run counts of the aged day are kept and labelled with expired_trace_runs (ADR-161)", runs=int(runs_daily[0][0]), expired=int(runs_daily[0][1]))
    check(ancient_daily == 0, "the rollup of the day of the deleted run records holds no runs")
    enforce_reports = [r for r in lib.reports(owner) if r["mode"] == "enforce"]
    check({r["tier"] for r in enforce_reports} >= {"artifacts", "trace", "run"}, "the reports show what was enforced, per tier", tiers=sorted({r["tier"] for r in enforce_reports}))
    actions_after = lib.audit_actions(owner)
    check(all(actions_after.count(a) >= 1 for a in ("retention.delete.artifacts", "retention.delete.trace", "retention.delete.run")),
          "one counts-only audit entry per enforced tier", entries={a: actions_after.count(a) for a in set(actions_after) if a.startswith("retention.delete")})
    check(int(lib.db_one("select count(*) from runs where workspace_id = $1", ws_b)) == 6 and int(lib.db_one("select count(*) from artifacts where workspace_id = $1", ws_b)) == 12,
          "workspace B (forever) lost nothing")
    check(base != "", "stack up")
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 3


def aged_batch(label: str, count: int, step: int = 60) -> list[lib.Seeded]:
    keys = lib.state()["keys"]
    runs = lib.ingest_runs(lib.H.main_url(), keys["main"], "main", label, count)
    ws = lib.workspace_uuid(lib.owner())
    lib.prepare_group(ws, runs, 120, step)
    lib.remember(label, runs)
    return runs


def processing_order(runs: list[lib.Seeded]) -> list[lib.Seeded]:
    """The oldest-first order the job processes: `age_runs` made later indexes older."""
    return list(reversed(runs))


def gate3() -> None:
    gate("gate3-crash")
    owner = lib.owner()
    ws = lib.workspace_uuid(owner)
    runs = aged_batch("crash", 20)
    order = processing_order(runs)
    target = order[10]  # the eleventh run the job meets
    ids = [r.uuid for r in runs]
    keys = lib.run_keys(ids)
    others_before = rows_digest(ids_of("young", "sibling", "doomed", "c_young", "b_aged"))
    control = {signature(r.uuid) for r in lib.seeded("aged")}  # the uninterrupted run of gate 2
    check(control == {("expired", (0,) * 8, ("artifacts", "trace"))}, "the control (gate 2's uninterrupted result) is available", control=sorted(map(str, control)))
    with lib.RowLock(target.uuid):
        proc = lib.start_retention("run-once", mode="enforce", name="crash")
        lib.wait_until("the retention process to wait on the held row lock", lambda: lib.retention_waiting(lib.retention_login()) or not proc.alive(), 120)
        check(proc.alive(), "the process is alive and waiting on a lock (it did not finish, which would defeat the test)")
        states = {r.run: lib.trace_state(owner, r.run) for r in runs}
        done = [r for r in order if states[r.run] == "expired"]
        deleting = [r for r in order if states[r.run] == "deleting"]
        untouched = [r for r in order if states[r.run] == "available"]
        info(expired=len(done), deleting=len(deleting), available=len(untouched), progress_rows=int(lib.db_one("select count(*) from retention_run_progress")))
        check(states[target.run] == "deleting", "API: the run being deleted reads trace_state deleting while the process waits", state=states[target.run])
        check(len(done) >= 1 and len(untouched) >= 1 and done == order[: len(done)], "mid-run for real: some runs are done, the rest untouched, in oldest-first order",
              done=len(done), deleting=len(deleting), untouched=len(untouched))
        check(all(int(lib.db_one("select count(*) from events where run_id = $1", r.uuid)) == EVENTS_PER_RUN for r in untouched),
              "the runs not yet reached are still complete")
        proc.kill()
    check(proc.popen.returncode == -signal.SIGKILL, "the process died by SIGKILL (no clean shutdown ran)", returncode=proc.popen.returncode)
    left = lib.db("select tier, detail_done, record_done from retention_run_progress where run_id = $1", target.uuid)
    check(bool(left) and not left[0]["detail_done"], "the killed run has a progress row that says its detail is not done", progress=[tuple(r) for r in left])
    check(lib.trace_state(owner, target.run) == "deleting", "after the crash the run is still marked deleting, not half-visible as available")

    code, rerun = lib.run_retention("run-once", mode="enforce", name="crash-rerun")
    summary = summary_of(rerun)
    info(exit=code, summary=summary)
    check(code == 0 and summary.get("failed") == "0", "the rerun exits 0 with failed=0")
    final = {signature(i) for i in ids}
    check(final == control, "the end state of the 20 runs equals the uninterrupted run's (state, row counts, tombstones)", signatures=sorted(map(str, final)))
    check(int(lib.db_one("select count(*) from runs where trace_state = 'deleting'")) == 0, "no run is left deleting")
    check(int(lib.db_one("select count(*) from retention_run_progress")) == 0, "no progress row is left")
    dup = lib.db("select run_id, tier, count(*) from retention_tombstones group by 1, 2 having count(*) > 1")
    check(not dup, "no duplicate tombstone")
    check(tombstones(ws, ids) == {"artifacts": 20, "trace": 20}, "exactly 20 artifact and 20 trace tombstones for the crash batch", got=tombstones(ws, ids))
    check(not any(lib.file_exists(k) for k in keys) and int(lib.db_one("select count(*) from artifacts where run_id = any($1::uuid[])", ids)) == 0,
          "no artifact row and no artifact file of the batch is left", files=len(keys))
    check(rows_digest(ids_of("young", "sibling", "doomed", "c_young", "b_aged")) == others_before, "everything outside the batch is byte-identical")
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 4


def gate4() -> None:
    gate("gate4-kill-switch-bounds")
    owner = lib.owner()
    ws = lib.workspace_uuid(owner)

    # ---- SIGTERM to the looping process: stops at the next batch boundary, exit code 0
    runs = aged_batch("sigterm", 12)
    order = processing_order(runs)
    target = order[4]
    with lib.RowLock(target.uuid):
        proc = lib.start_retention("run", mode="enforce", mode_via_file=True, name="sigterm", env={"ABB_RETENTION_TICK_INTERVAL_SECONDS": "3600"})
        lib.wait_until("the looping process to wait on the held lock", lambda: lib.retention_waiting(lib.retention_login()) or not proc.alive(), 120)
        check(proc.alive(), "the looping retention process is waiting on a lock")
        proc.signal(signal.SIGTERM)
    try:
        code = proc.wait(120)
    except Exception:
        proc.kill()
        code = -999
    check(code == 0, "SIGTERM: the process finishes the batch in hand and exits 0 (the documented graceful stop)", exit=code)
    states = {r.run: lib.trace_state(owner, r.run) for r in order}
    after_target = [states[r.run] for r in order[5:]]
    check(set(after_target) == {"available"}, "SIGTERM: runs after the batch in hand were not started (still available, complete)", after=sorted(set(after_target)))
    check(all(states[r.run] == "expired" for r in order[:4]), "SIGTERM: the runs before it are complete", states=[states[r.run] for r in order[:5]])
    open_runs = [r for r in order if states[r.run] == "deleting"]
    with_progress = [r for r in open_runs if int(lib.db_one("select count(*) from retention_run_progress where run_id = $1", r.uuid)) == 1]
    check(len(open_runs) == len(with_progress), "SIGTERM: any run left deleting has its progress row (nothing half-visible without a record)", deleting=len(open_runs))
    code, rerun = lib.run_retention("run-once", mode="enforce", name="sigterm-rerun")
    check(code == 0 and {trace for trace in (lib.trace_state(owner, r.run) for r in order)} == {"expired"}, "a rerun finishes the work: every run expired")
    check(int(lib.db_one("select count(*) from runs where trace_state = 'deleting'")) == 0, "no run is left deleting")

    # ---- the mode file: switching to dry_run (and off) stops enforcement at the next batch
    runs = aged_batch("modeswitch", 12)
    order = processing_order(runs)
    target = order[4]
    with lib.RowLock(target.uuid):
        proc = lib.start_retention("run-once", mode="enforce", mode_via_file=True, name="modeswitch")
        lib.wait_until("the process to wait on the held lock", lambda: lib.retention_waiting(lib.retention_login()) or not proc.alive(), 120)
        check(proc.alive(), "the retention process is waiting on a lock")
        lib.write_mode(proc.cwd, "dry_run")  # the kill switch: the file is re-read before every batch
    code = proc.wait(120)
    summary = summary_of(proc)
    info(exit=code, summary=summary)
    check(code == 0 and summary.get("halted") == "mode", "mode set to dry_run mid-run: the tick halts at the next batch with halted=mode", summary=summary.get("halted"))
    states = [lib.trace_state(owner, r.run) for r in order]
    check(states[5:] == ["available"] * 7, "no run after the batch in hand was touched", states=states)
    before = lib.counts()
    code, dry = lib.run_retention("run-once", mode="dry_run", name="modeswitch-dry")
    after = lib.counts()
    check(code == 0 and {k for k in after if after[k] != before[k]} <= {"retention_reports"}, "a dry_run tick after the switch deletes nothing")
    code, off = lib.run_retention("run-once", mode="off", name="modeswitch-off")
    check(code == 0 and summary_of(off).get("mode") == "off" and lib.counts() == after, "mode off: the process does nothing (no row changes, not even a report)")
    code, rest = lib.run_retention("run-once", mode="enforce", name="modeswitch-finish")
    check(code == 0 and {lib.trace_state(owner, r.run) for r in order} == {"expired"}, "back in enforce the remaining runs are finished")

    # ---- the per-tick bound
    runs = aged_batch("bounds", 12)
    expected = [5, 5, 2]
    progress: list[int] = []
    truncated_flags: list[str | None] = []
    for index, step in enumerate(expected):
        code, tick = lib.run_retention("run-once", mode="enforce", name=f"bounds-{index}", env={"ABB_RETENTION_MAX_RUNS_PER_TICK": "5"})
        s = summary_of(tick)
        check(code == 0, f"bounded tick {index + 1} exits 0")
        progress.append(sum(1 for r in runs if lib.trace_state(owner, r.run) == "expired"))
        truncated_flags.append(s.get("truncated"))
    check(progress == [5, 10, 12], "ABB_RETENTION_MAX_RUNS_PER_TICK=5: ticks expire 5, 5 and 2 runs (cumulative 5, 10, 12)", cumulative=progress)
    check(truncated_flags[0] not in (None, "0") and truncated_flags[1] not in (None, "0"), "the summaries of the first two ticks say truncated", truncated=truncated_flags)
    flagged = [r for r in lib.reports(owner) if r["mode"] == "enforce" and r["tier"] == "trace" and r["truncated"]]
    check(len(flagged) >= 2, "the reports of those ticks are marked truncated (the figures are a lower bound)", reports=len(flagged))
    check(ws is not None, "done")
    lib.finish()
