#!/usr/bin/env python3
"""Record a multi-agent job with Agent Black Box.

    uv run --project packages/sdk-python python examples/multi-agent/agent.py [--skew-ms 1500] [--fail-tool]

A planner delegates to a researcher and a coder in threads; the coder has a tester run in a separate
OS process (joined through BLACKBOX_TRACE_CONTEXT); a reviewer runs as an asyncio task. Models are
fakes (fixed token counts), so the run is the same every time and needs no network.

Needs BLACKBOX_API_KEY (a project key with events:write) and BLACKBOX_ENDPOINT, or --offline.
Prints one JSON line with the run id. `--skew-ms N` makes the tester's clock N ms behind, to show
that the waterfall still nests it under the coder.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackbox import BlackBox, PayloadMode

from multi_agent.planner import run_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skew-ms", type=float, default=0.0, help="tester clock this far behind")
    parser.add_argument(
        "--fail-tool", action="store_true", help="the researcher's search tool fails once"
    )
    parser.add_argument(
        "--pace", type=float, default=1.0, help="scale every pretend delay (0 = none)"
    )
    parser.add_argument(
        "--full-payloads",
        action="store_true",
        help="capture delegation inputs, results and responses (redacted); default is metadata only",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="record nothing remotely (the tester then sends nothing)",
    )
    args = parser.parse_args(argv)
    mode = PayloadMode.FULL if args.full_payloads else PayloadMode.METADATA_ONLY
    bb = BlackBox(
        project="multi-agent-demo",
        agent_id="planner",
        mode="offline" if args.offline else "http",
        payload_mode=mode,
    )
    outcome = run_job(
        bb,
        skew_ms=args.skew_ms,
        fail_tool=args.fail_tool,
        pace=args.pace,
        payload_mode=mode.value,
        child_env={"BLACKBOX_MODE": "disabled"} if args.offline else {},
    )
    flushed = bb.shutdown(15)
    print(
        json.dumps(
            {"run_id": outcome.run_id, "ok": outcome.ok, "flushed": flushed, "stats": bb.stats()}
        )
    )
    return 0 if outcome.ok and flushed else 1


if __name__ == "__main__":
    raise SystemExit(main())
