#!/usr/bin/env python3
"""A scripted release-ops agent, recorded with Agent BlackBox: the run that gives the security findings something to find.

    uv run --project packages/sdk-python python examples/python/ops_agent.py            # scripted, deterministic
    uv run --project packages/sdk-python python examples/python/ops_agent.py --offline  # nothing leaves the process

The agent is asked to tidy old releases. It looks at the release directory, opens a `.env` file, deletes the old releases with a
forced git clean, and force-pushes `main`. The two harmful commands are RECORDED with the classifier's verdict and never
executed (the same technique as scripts/security_e2e_plant.py); only harmless commands (`ls`) really run, inside a throwaway
directory. The model is a fixed script with made-up prices, so the run is the same every time and needs no key or network.

Needs BLACKBOX_API_KEY (events:write) and BLACKBOX_ENDPOINT, or --offline. Prints one JSON line with the run id.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from blackbox import BlackBox, PayloadMode, Run
from blackbox.coding import CodingRecorder

from demo_clock import SCRIPTED_PRICES, DemoClock, hours, latency_seconds, scripted_cost

PROVIDER = "scripted"
# (input tokens, output tokens) per scripted model turn
TURNS = ((520, 40), (880, 64), (1210, 52), (1460, 38))


def plan(
    run: Run,
    turn: int,
    pace: float,
    *,
    model: str = "scripted-small",
    scale: float = 1.0,
    clock: DemoClock | None = None,
) -> None:
    tokens_in, tokens_out = (round(n * scale) for n in TURNS[turn])
    with run.llm_call(PROVIDER, model) as call:
        if clock is not None:
            clock.advance(latency_seconds(tokens_out))
        else:
            time.sleep(0.5 * pace)
        call.record_usage(
            tokens_in, tokens_out, cost_usd=scripted_cost(model, tokens_in, tokens_out)
        )


def record_only(run: Run, rec: CodingRecorder, command: str, exit_code: int = 0) -> None:
    """A shell span carrying the classifier's verdict, without running anything."""
    verdict = rec.classify(command)
    span = run.span(
        rec.sanitize(command),
        kind="shell",
        attributes={
            "shell.cwd": "/srv/app",
            "shell.risk_class": verdict.risk_class,
            "shell.category": verdict.category,
        },
    )
    span.start()
    span.set_attributes({"shell.exit_code": exit_code, "shell.duration_ms": 12.0})
    span.end("success" if exit_code == 0 else "error")


def workspace(dest: Path) -> Path:
    root = dest / "app"
    (root / "releases" / "2026-09-01").mkdir(parents=True)
    (root / "releases" / "2026-09-15").mkdir()
    (root / ".env").write_text("# demo placeholder, not a secret\nDEMO_MODE=1\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for key, value in (("user.email", "agent@example.test"), ("user.name", "agent")):
        subprocess.run(["git", "config", key, value], cwd=root, check=True, capture_output=True)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--offline", action="store_true", help="record nothing remotely (events stay in memory)"
    )
    parser.add_argument(
        "--pace", type=float, default=0.0, help="seconds scale for pretend latencies (0 = none)"
    )
    parser.add_argument(
        "--started-ago",
        type=float,
        default=None,
        metavar="HOURS",
        help="record the run as started this many hours ago, with believable latencies (virtual time; see demo_clock.py)",
    )
    parser.add_argument(
        "--tokens-scale", type=float, default=1.0, help="multiply every token count"
    )
    parser.add_argument(
        "--model-name",
        choices=sorted(SCRIPTED_PRICES),
        default="scripted-small",
        help="made-up model",
    )
    args = parser.parse_args(argv)
    clock = DemoClock(hours(args.started_ago)) if args.started_ago is not None else None
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    bb = BlackBox(
        project="ops-agent-demo",
        agent_id="ops-agent",
        mode="offline" if args.offline else "http",
        payload_mode=PayloadMode.METADATA_ONLY,
    )
    with tempfile.TemporaryDirectory(prefix="abb-ops-demo-") as scratch:
        root = workspace(Path(scratch))
        with bb.run("Clean up old releases", metadata={"model": args.model_name}) as run:
            run_id = run.run_id
            rec = CodingRecorder(bb, run, root)

            def think(turn: int) -> None:
                plan(
                    run,
                    turn,
                    args.pace,
                    model=args.model_name,
                    scale=args.tokens_scale,
                    clock=clock,
                )

            think(0)
            rec.run_command("ls releases")
            think(1)
            rec.read_file(".env")  # a secret file read
            think(2)
            record_only(run, rec, "git clean -fdx")  # destructive, recorded and never run
            think(3)
            record_only(
                run, rec, "git push --force origin main", 128
            )  # risky git, recorded and never run
    flushed = bb.shutdown(15)
    print(json.dumps({"run_id": run_id, "flushed": flushed, "stats": bb.stats()}))
    return 0 if (flushed or args.offline) else 1


if __name__ == "__main__":
    sys.exit(main())
