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

MODEL = ("scripted", "scripted-small")
PRICE_IN, PRICE_OUT = 3.0, 15.0  # made-up USD per million tokens
# (input tokens, output tokens) per scripted model turn
TURNS = ((520, 40), (880, 64), (1210, 52), (1460, 38))


def plan(run: Run, turn: int, pace: float) -> None:
    tokens_in, tokens_out = TURNS[turn]
    with run.llm_call(*MODEL) as call:
        time.sleep(0.5 * pace)
        call.record_usage(
            tokens_in,
            tokens_out,
            cost_usd=round((tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1e6, 6),
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
    args = parser.parse_args(argv)
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    bb = BlackBox(
        project="ops-agent-demo",
        agent_id="ops-agent",
        mode="offline" if args.offline else "http",
        payload_mode=PayloadMode.METADATA_ONLY,
    )
    with tempfile.TemporaryDirectory(prefix="abb-ops-demo-") as scratch:
        root = workspace(Path(scratch))
        with bb.run("Clean up old releases", metadata={"model": MODEL[1]}) as run:
            run_id = run.run_id
            rec = CodingRecorder(bb, run, root)
            plan(run, 0, args.pace)
            rec.run_command("ls releases")
            plan(run, 1, args.pace)
            rec.read_file(".env")  # a secret file read
            plan(run, 2, args.pace)
            record_only(run, rec, "git clean -fdx")  # destructive, recorded and never run
            plan(run, 3, args.pace)
            record_only(
                run, rec, "git push --force origin main", 128
            )  # risky git, recorded and never run
    flushed = bb.shutdown(15)
    print(json.dumps({"run_id": run_id, "flushed": flushed, "stats": bb.stats()}))
    return 0 if (flushed or args.offline) else 1


if __name__ == "__main__":
    sys.exit(main())
