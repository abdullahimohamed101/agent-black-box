"""Stages of scripts/retention-e2e.sh (Phase 19b step 6, gates 1-8 of D6). Run through `scripts/retention-e2e.sh`, in the API's environment:

    provision      three workspaces, people for the roles, projects, API keys (generated, local-only)
    seed           runs with events and artifacts through the REAL ingestion API, then aged by the owner's SQL (the harness)
    gate1          dry run: reports with the right counts, NOTHING deleted (rows, files)
    gate2          enforce: policy through the API (role-gated, audited, floor), order, trace states, quota counter, rollups, tombstones
    gate3          crash safety: SIGKILL while the process waits on a row lock, rerun, same end state as an uninterrupted run
    gate4          kill switch (SIGTERM, mode file) and the per-tick bounds
    gate5          privileges of the runtime and retention logins over real connections; no retention credentials in the API's environment
    gate6          purge-project / purge-workspace
    ui-prepare / ui-verify    the browser spec's data and its database checks (gate 7)
    gate8          cost: ingestion latency idle vs while enforcement runs in the background
    summary        the pass/fail table and the log scan
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import retention_e2e_lib as lib  # noqa: E402
from retention_e2e_lib import check, die, finish, gate, info  # noqa: E402

# Lines in the logs that are expected: (substring, why). Everything else containing a traceback or a failed job is a failure.
LOG_ALLOW: list[tuple[str, str]] = []

GROUPS_A = {"young": (6, 2), "mid": (6, 45), "aged": (12, 120), "ancient": (6, 400)}  # (runs, age in days), project `main`


def project_id(client, slug: str) -> str:  # type: ignore[no-untyped-def]
    response = client.get("/v1/projects")
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["slug"] == slug:
            return str(item["id"])
    die(f"no project {slug}")
    return ""


def provision() -> None:
    gate("provision")
    suffix = os.environ["E2E_SUFFIX"]
    ws_a, ws_b, ws_c = lib.H.SLUG, lib.H.SLUG2, f"retention-doomed-{suffix}"
    people = {
        "owner": f"owner-{suffix}@retention.test", "admin": f"admin-{suffix}@retention.test", "developer": f"developer-{suffix}@retention.test",
        "viewer": f"viewer-{suffix}@retention.test", "other_admin": f"owner-b-{suffix}@other.test",
    }
    # Two people of the doomed workspace without a session: one only there (the users rule removes them), one also in workspace B.
    c_only, c_shared = f"conly-{suffix}@doomed.test", f"cshared-{suffix}@doomed.test"
    lib.H.cli("create-workspace", "--name", "Retention E2E", "--slug", ws_a)
    lib.H.cli("create-workspace", "--name", "Retention Other", "--slug", ws_b)
    lib.H.cli("create-workspace", "--name", "Retention Doomed", "--slug", ws_c)
    for role, address in people.items():
        workspace = ws_b if role == "other_admin" else ws_a
        member_role = {"owner": "OWNER", "admin": "ADMIN", "developer": "DEVELOPER", "viewer": "VIEWER", "other_admin": "OWNER"}[role]
        lib.H.cli("add-member", "--workspace", workspace, "--email", address, "--role", member_role)
    lib.H.cli("add-member", "--workspace", ws_c, "--email", c_only, "--role", "OWNER")
    lib.H.cli("add-member", "--workspace", ws_c, "--email", c_shared, "--role", "ADMIN")
    lib.H.cli("add-member", "--workspace", ws_b, "--email", c_shared, "--role", "VIEWER")
    sessions = {role: lib.H.session_token(address) for role, address in people.items()}
    lib.save(people=people, sessions=sessions, main_urls=[os.environ["E2E_API_URL"]], c_only=c_only, c_shared=c_shared, ws_c=ws_c)
    keys: dict[str, str] = {}
    for workspace, project in ((ws_a, "main"), (ws_a, "sibling"), (ws_a, "doomed"), (ws_a, "bench"), (ws_b, "other"), (ws_c, "cproj")):
        lib.H.cli("create-project", "--workspace", workspace, "--name", project, "--slug", project, "--capture-mode", "full")
        keys[project] = lib.H.cli("create-key", "--workspace", workspace, "--project", project, "--scopes", "events:write", "artifacts:write", "runs:read")
        if not keys[project]:
            die(f"no key for {project}")
    lib.save(keys=keys)
    ids = {p: project_id(lib.owner(), p) for p in ("main", "sibling", "doomed", "bench")}
    ids["other"] = project_id(lib.other_owner(), "other")
    lib.save(project_ids=ids)
    check(lib.owner().get("/v1/me").status_code == 200, "the owner session works")
    check(True, "provisioned: 3 workspaces, 7 people, 6 projects with keys")
    finish()


def seed() -> None:
    gate("seed")
    base = lib.H.main_url()
    keys = lib.state()["keys"]
    ws_a = lib.workspace_uuid(lib.owner())
    ws_b = lib.workspace_uuid(lib.other_owner())
    groups: dict[str, list[lib.Seeded]] = {}
    for name, (count, _) in GROUPS_A.items():
        groups[name] = lib.ingest_runs(base, keys["main"], "main", name, count)
    groups["sibling"] = lib.ingest_runs(base, keys["sibling"], "sibling", "sibling", 4)
    groups["doomed"] = lib.ingest_runs(base, keys["doomed"], "doomed", "doomed", 4)
    groups["b_aged"] = lib.ingest_runs(base, keys["other"], "other", "b_aged", 6)
    groups["c_young"] = lib.ingest_runs(base, keys["cproj"], "cproj", "c_young", 3)
    everything = [r for g in groups.values() for r in g]
    lib.wait_idle(everything)
    before = {ws: lib.run_days([r for g in grp for r in g]) for ws, grp in (
        (ws_a, [groups[n] for n in (*GROUPS_A, "sibling", "doomed")]), (ws_b, [groups["b_aged"]]))}
    for name, (_, days) in GROUPS_A.items():
        lib.age_runs(groups[name], days)
    lib.age_runs(groups["b_aged"], 120)
    lib.rebuild_rollups(ws_a, before[ws_a] | lib.run_days([r for n in (*GROUPS_A, "sibling", "doomed") for r in groups[n]]))
    lib.rebuild_rollups(ws_b, before[ws_b] | lib.run_days(groups["b_aged"]))
    for name, runs in groups.items():
        lib.remember(name, runs)
    events = int(lib.db_one("select count(*) from events"))
    artifacts = int(lib.db_one("select count(*) from artifacts"))
    check(sum(len(g) for g in groups.values()) == 6 + 6 + 12 + 6 + 4 + 4 + 6 + 3, "all runs were accepted by the real ingestion API")
    check(events == 47 * 4 and artifacts == 47 * 2, "4 events and 2 artifacts per run are stored", events=events, artifacts=artifacts)
    ages = {n: int(lib.db_one("select extract(day from now() - min(created_at))::int from runs where id = any($1::uuid[])", [r.uuid for r in g])) for n, g in groups.items()}
    info(runs_per_group={n: len(g) for n, g in groups.items()}, age_days=ages)
    finish()


def summary() -> None:
    gate("summary")
    rows = [json.loads(line) for line in lib.RESULTS.read_text().splitlines() if line.strip()]
    by_gate: dict[str, list[bool]] = {}
    for row in rows:
        by_gate.setdefault(row["gate"], []).append(row["ok"])
    print("\n  gate                      checks  passed  failed")
    for name, oks in by_gate.items():
        print(f"  {name:<24} {len(oks):>7}  {sum(oks):>6}  {len(oks) - sum(oks):>6}")
    bad: dict[str, list[str]] = {}
    scanned = 0
    for log in lib.all_logs():
        scanned += 1
        for line in lib.log_lines(log, "Traceback", "job failed", "Unhandled exception", "ERROR:", '"level": "ERROR"', '"level":"ERROR"'):
            if any(allowed in line for allowed, _ in LOG_ALLOW):
                continue
            bad.setdefault(log.name, []).append(line)
    check(not bad, "no 'job failed', Traceback or ERROR line in any process log (API, worker, every retention process)", logs=scanned,
          offenders={k: v[:3] for k, v in bad.items()})
    failures = [r for r in rows if not r["ok"]]
    print(f"\n  TOTAL {len(rows)} checks, {len(rows) - len(failures)} passed, {len(failures)} failed")
    for row in failures:
        print(f"  FAIL [{row['gate']}] {row['label']}")
    finish()
    if failures:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: retention_e2e_check.py <stage>")
    stage = sys.argv[1]
    if stage == "provision":
        provision()
    elif stage == "seed":
        seed()
    elif stage == "summary":
        summary()
    elif stage in {"gate1", "gate2", "gate3", "gate4"}:
        import retention_e2e_enforce  # noqa: PLC0415

        getattr(retention_e2e_enforce, stage)()
    elif stage in {"gate5", "gate6"}:
        import retention_e2e_purge  # noqa: PLC0415

        getattr(retention_e2e_purge, stage)()
    elif stage in {"ui-prepare", "ui-verify", "gate8"}:
        import retention_e2e_ui  # noqa: PLC0415

        getattr(retention_e2e_ui, stage.replace("-", "_"))()
    else:
        die(f"unknown stage {stage}")
    info(stage=stage, done=True)


if __name__ == "__main__":
    main()
