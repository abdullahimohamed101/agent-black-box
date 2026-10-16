"""Stages of scripts/obs-e2e.sh (Phase 19c step 6). Run through `scripts/obs-e2e.sh`, in the API's environment:

    provision   a workspace, an owner and a developer, sessions (all generated, local-only)
    gate1       /metrics: absent without a token, one 401, the type and headers, parses, bounded, no tenant text, API and worker
    gate2       every catalogue family moves by exactly the traffic driven
    gate3       readiness and the breaker against deliberately broken processes
    gate5       real Prometheus and Grafana from the pinned images
    gate6       tracing against a local OTLP receiver
    gate7       cost of metrics and tracing (informational)
    summary     gate 4 (the logs of every process) and the pass/fail table
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import obs_e2e_lib as lib  # noqa: E402
from obs_e2e_lib import base, check, die, finish, gate, info  # noqa: E402


def provision() -> None:
    gate("provision")
    suffix = os.environ["E2E_SUFFIX"]
    ws = lib.base.SLUG
    people = {"owner": f"owner-{suffix}@obs.test", "developer": f"developer-{suffix}@obs.test"}
    lib.base.cli("create-workspace", "--name", "Obs E2E", "--slug", ws)
    for role, address in people.items():
        lib.base.cli("add-member", "--workspace", ws, "--email", address, "--role", "OWNER" if role == "owner" else "DEVELOPER")
    sessions = {role: base.session_token(address) for role, address in people.items()}
    base.save(people=people, sessions=sessions, main_urls=[lib.API_URL], sentinels={}, secrets={})
    for role, token in sessions.items():
        lib.remember_secret(f"session-{role}", token)
    reply = lib.person("owner").get("/v1/me")
    check(reply.status_code == 200, "the owner session works", status=reply.status_code)
    lib.remember_secret("runtime-db-password", os.environ["E2E_RUNTIME_DB_URL"].split(":", 2)[2].split("@", 1)[0])
    lib.remember_secret("metrics-token", lib.metrics_token())
    check(True, "provisioned: a workspace, 2 people, sessions minted", people=len(people))
    finish()


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: obs_e2e_check.py <stage>")
    stage = sys.argv[1]
    if stage == "provision":
        provision()
    elif stage == "summary":
        import obs_e2e_summary  # noqa: PLC0415

        obs_e2e_summary.summary()
    elif stage == "gate1":
        import obs_e2e_endpoint  # noqa: PLC0415

        obs_e2e_endpoint.gate1()
    elif stage == "gate2":
        import obs_e2e_traffic  # noqa: PLC0415

        obs_e2e_traffic.gate2()
    elif stage == "gate3":
        import obs_e2e_ready  # noqa: PLC0415

        obs_e2e_ready.gate3()
    elif stage == "gate5":
        import obs_e2e_prom  # noqa: PLC0415

        obs_e2e_prom.gate5()
    elif stage == "gate6":
        import obs_e2e_trace  # noqa: PLC0415

        obs_e2e_trace.gate6()
    elif stage == "gate7":
        import obs_e2e_bench  # noqa: PLC0415

        obs_e2e_bench.gate7()
    else:
        die(f"unknown stage {stage}")
    info(stage=stage, done=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        for proc in base._started:  # a gate's own processes never outlive it
            proc.stop()
