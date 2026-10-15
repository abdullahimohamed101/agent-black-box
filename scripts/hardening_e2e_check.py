"""Gates of scripts/hardening-e2e.sh (Phase 19a step 7). Run through `scripts/hardening-e2e.sh`, in the API's environment:

    provision   workspaces, people and sessions (all generated, local-only)
    gate1       limits hold across processes (ADR-152), with a control (sharing off)
    gate2       failed-auth flood (ADR-153), spoof resistance, audit, the real SDK
    gate3       quotas (ADR-154)
    gate4       edge (ADR-155)
    gate5       identity (ADR-156/157)
    gate6       alert ports and search fairness (ADR-158)
    gate7       cost of everything on vs off (docs/benchmarks/phase-19a-hardening-e2e.md)
    ui-prepare / ui-verify   the browser spec's data and its database checks
    summary     the pass/fail table and the log scan
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import hardening_e2e_lib as lib  # noqa: E402
from hardening_e2e_lib import check, die, finish, gate, info  # noqa: E402

# Lines in the server logs that are expected: (substring, why). Everything else containing a traceback or a failed job is a failure.
# Logs of the processes the gates start ON PURPOSE to fail (production start with a privileged role) are named `expected-*.log`
# and are checked for exactly the fixed message instead of being scanned.
LOG_ALLOW: list[tuple[str, str]] = []


def provision() -> None:
    gate("provision")
    suffix = os.environ["E2E_SUFFIX"]
    ws, ws2 = lib.SLUG, lib.SLUG2
    people = {
        "owner": f"owner-{suffix}@hardening.test", "admin": f"admin-{suffix}@hardening.test", "developer": f"developer-{suffix}@hardening.test",
        "viewer": f"viewer-{suffix}@hardening.test", "inviter": f"inviter-{suffix}@hardening.test", "other_admin": f"admin2-{suffix}@other.test",
    }
    lib.cli("create-workspace", "--name", "Hardening E2E", "--slug", ws)
    lib.cli("create-workspace", "--name", "Hardening Other", "--slug", ws2)
    for role, address in people.items():
        workspace = ws2 if role == "other_admin" else ws
        member_role = {"owner": "OWNER", "admin": "ADMIN", "developer": "DEVELOPER", "viewer": "VIEWER", "inviter": "ADMIN", "other_admin": "ADMIN"}[role]
        lib.cli("add-member", "--workspace", workspace, "--email", address, "--role", member_role)
    sessions = {role: lib.session_token(address) for role, address in people.items()}
    ports = [int(p) for p in os.environ["E2E_API_PORTS"].split(",")]
    lib.save(people=people, sessions=sessions, main_urls=[f"http://127.0.0.1:{p}" for p in ports])
    # Both main processes see the same world: the same owner session works through each.
    for url in lib.main_urls():
        reply = lib.person("owner", url).get("/v1/me")
        check(reply.status_code == 200, f"the owner session works through {url}")
    check(True, "provisioned: 2 workspaces, 6 people, sessions minted", people=len(people))
    finish()


def summary() -> None:
    gate("summary")
    rows = [json.loads(line) for line in lib.RESULTS.read_text().splitlines() if line.strip()]
    by_gate: dict[str, list[bool]] = {}
    for row in rows:
        by_gate.setdefault(row["gate"], []).append(row["ok"])
    print("\n  gate                 checks  passed  failed")
    for name, oks in by_gate.items():
        print(f"  {name:<20} {len(oks):>6}  {sum(oks):>6}  {len(oks) - sum(oks):>6}")
    # No unexpected job failure or traceback in any process log (the worker, every API process).
    bad: dict[str, list[str]] = {}
    scanned = 0
    for log in lib.all_logs():
        if log.name.startswith("expected-"):
            continue
        scanned += 1
        for line in lib.log_lines(log, "Traceback", "job failed", "Unhandled exception", "ERROR:"):
            if any(allowed in line for allowed, _ in LOG_ALLOW):
                continue
            bad.setdefault(log.name, []).append(line)
    for expected in lib.WORK.glob("expected-*.log"):
        text = expected.read_text(errors="replace")
        check("postgresql" not in text.lower() and "password" not in text.lower(), f"{expected.name} carries no connection string or password")
    check(not bad, "no 'job failed', Traceback or ERROR line in any process log", logs=scanned, offenders={k: v[:3] for k, v in bad.items()})
    failures = [r for r in rows if not r["ok"]]
    print(f"\n  TOTAL {len(rows)} checks, {len(rows) - len(failures)} passed, {len(failures)} failed")
    for row in failures:
        print(f"  FAIL [{row['gate']}] {row['label']}")
    finish()
    if failures:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: hardening_e2e_check.py <stage>")
    stage = sys.argv[1]
    if stage == "provision":
        provision()
    elif stage == "summary":
        summary()
    elif stage == "gate1":
        import hardening_e2e_limits  # noqa: PLC0415

        hardening_e2e_limits.gate1()
    elif stage == "gate2":
        import hardening_e2e_limits  # noqa: PLC0415

        hardening_e2e_limits.gate2()
    elif stage == "gate3":
        import hardening_e2e_quotas  # noqa: PLC0415

        hardening_e2e_quotas.gate3()
    elif stage == "gate4":
        import hardening_e2e_edge  # noqa: PLC0415

        hardening_e2e_edge.gate4()
    elif stage in {"gate5", "gate6", "ui-prepare", "ui-verify"}:
        import hardening_e2e_identity  # noqa: PLC0415

        getattr(hardening_e2e_identity, stage.replace("-", "_"))()
    elif stage == "gate7":
        import hardening_e2e_bench  # noqa: PLC0415

        hardening_e2e_bench.gate7()
    else:
        die(f"unknown stage {stage}")
    info(stage=stage, done=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        for proc in lib._started:  # a gate's own API processes never outlive it
            proc.stop()
