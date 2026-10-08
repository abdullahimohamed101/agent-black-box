#!/usr/bin/env python3
"""Run the coding-agent demo and record it with Agent Black Box.

    uv run --project packages/sdk-python python examples/coding-agent/run_demo.py            # scripted, deterministic
    ANTHROPIC_API_KEY=... uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --model anthropic

Needs BLACKBOX_API_KEY (a project key with events:write and artifacts:write) and BLACKBOX_ENDPOINT. The demo
turns on full payload capture (diffs and terminal output are redacted, then uploaded as artifacts); that is a
deliberate opt-in, see docs/SECURITY.md and ADR-031. Prints one JSON line with the run id.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackbox import BlackBox, PayloadMode
from blackbox.coding import CodingRecorder

from coding_agent.agent import run_agent
from coding_agent.models import ScriptedModel
from coding_agent.workspace import prepare_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", choices=["scripted", "anthropic"], default="scripted")
    parser.add_argument(
        "--workdir", type=Path, help="where to build the working copy (default: a temp dir)"
    )
    parser.add_argument(
        "--offline", action="store_true", help="record nothing remotely (events stay in memory)"
    )
    args = parser.parse_args(argv)

    # `python` in recorded commands must be the interpreter running the demo.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    if args.model == "anthropic":
        from coding_agent.anthropic_model import AnthropicModel

        model = AnthropicModel()
    else:
        model = ScriptedModel()

    with tempfile.TemporaryDirectory(prefix="abb-coding-demo-") as scratch:
        workdir = args.workdir or Path(scratch)
        root = prepare_workspace(workdir)
        bb = BlackBox(
            project="coding-agent-demo",
            agent_id="coding-agent",
            mode="offline" if args.offline else "http",
            payload_mode=PayloadMode.FULL,
        )
        with bb.run("Fix OAuth session expiry", metadata={"model": model.name}) as run:
            rec = CodingRecorder(bb, run, root)
            result = run_agent(bb, run, rec, model)
            run_id = run.run_id
        flushed = bb.shutdown(15)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "tests_passed": result.tests_passed,
                    "retries": result.retries,
                    "turns": result.turns,
                    "flushed": flushed,
                    "stats": bb.stats(),
                }
            )
        )
    return 0 if result.tests_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
